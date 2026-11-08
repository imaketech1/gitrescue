"""Repository state snapshots, backup refs and restore.

A snapshot captures everything GitRescue's tools can change locally:
HEAD (symbolic or detached), every local branch tip, the stash list, the
index tree and a tree of the whole working tree (tracked + untracked, minus
ignored files). Backups pin every object of a snapshot under
``refs/gitrescue/backups/<id>/`` so nothing in it can become unreachable, and
store the snapshot itself as JSON under ``.git/gitrescue/backups/``.

This module is infrastructure, not an agent tool, so it may call git with the
guard disabled where it needs plumbing commands (update-ref, read-tree).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from gitrescue.git import git_dir, git_ok, in_progress_operation, run_git

BACKUP_NS = "refs/gitrescue/backups"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_IDENTITY = {
    "GIT_AUTHOR_NAME": "GitRescue",
    "GIT_AUTHOR_EMAIL": "gitrescue@localhost",
    "GIT_COMMITTER_NAME": "GitRescue",
    "GIT_COMMITTER_EMAIL": "gitrescue@localhost",
}


@dataclass
class RepoState:
    head_ref: str | None  # "refs/heads/<name>" or None when detached
    head_sha: str | None
    branches: dict[str, str] = field(default_factory=dict)
    stash: list[list[str]] = field(default_factory=list)  # [[sha, message], ...] newest first
    index_tree: str | None = None  # None when the index has unmerged entries
    worktree_tree: str | None = None
    in_progress: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RepoState:
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Backup:
    id: str
    label: str
    created: str
    state: RepoState

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "created": self.created, "state": self.state.to_dict()}


def _g(repo: Path, *args: str, **kw) -> str:
    return git_ok(repo, *args, _guard=False, **kw)


def worktree_tree(repo: Path) -> str:
    """Tree object of the working tree (tracked + untracked, honouring .gitignore), without touching the real index."""
    gd = git_dir(repo)
    tmp = gd / f"gitrescue-index-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        real = gd / "index"
        if real.exists():
            shutil.copyfile(real, tmp)
        env = {"GIT_INDEX_FILE": str(tmp)}
        _g(repo, "add", "-A", "--", ".", env=env)
        return _g(repo, "write-tree", env=env).strip()
    finally:
        tmp.unlink(missing_ok=True)


def capture_state(repo: Path) -> RepoState:
    repo = Path(repo)
    sym = run_git(repo, "symbolic-ref", "--quiet", "HEAD")
    head_ref = sym.stdout.strip() if sym.ok else None
    head = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    branches = {}
    for line in _g(repo, "for-each-ref", "--format=%(refname)\x1f%(objectname)", "refs/heads").splitlines():
        ref, sha = line.split("\x1f")
        branches[ref[len("refs/heads/") :]] = sha
    stash: list[list[str]] = []
    if run_git(repo, "rev-parse", "--verify", "--quiet", "refs/stash").ok:
        out = _g(repo, "reflog", "show", "--format=%H\x1f%gs", "refs/stash")
        stash = [line.split("\x1f", 1) for line in out.splitlines() if line]
    wt = run_git(repo, "write-tree", _guard=False)
    return RepoState(
        head_ref=head_ref,
        head_sha=head.stdout.strip() if head.ok else None,
        branches=branches,
        stash=stash,
        index_tree=wt.stdout.strip() if wt.ok else None,
        worktree_tree=worktree_tree(repo),
        in_progress=in_progress_operation(repo),
    )


def _tree_commit(repo: Path, tree: str, message: str) -> str:
    return _g(repo, "commit-tree", tree, "-m", message, env=_IDENTITY).strip()


def _backup_dir(repo: Path) -> Path:
    d = git_dir(repo) / "gitrescue" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_backup_id(prefix: str | None = None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix or 'manual'}-{stamp}-{secrets.token_hex(2)}"


def create_backup(repo: Path, label: str, backup_id: str | None = None, state: RepoState | None = None) -> Backup:
    repo = Path(repo)
    bid = backup_id or new_backup_id()
    if not _ID_RE.match(bid):
        raise ValueError(f"invalid backup id {bid!r}")
    if backup_exists(repo, bid):
        raise ValueError(f"backup id {bid!r} already exists")
    state = state or capture_state(repo)
    ns = f"{BACKUP_NS}/{bid}"
    refs: list[tuple[str, str]] = []
    if state.head_sha:
        refs.append((f"{ns}/HEAD", state.head_sha))
    for name, sha in state.branches.items():
        refs.append((f"{ns}/heads/{name}", sha))
    for i, (sha, _msg) in enumerate(state.stash):
        refs.append((f"{ns}/stash/{i}", sha))
    # Pin remote-tracking tips too (not restored, but keeps force-pushed-over commits recoverable).
    for line in _g(repo, "for-each-ref", "--format=%(refname)\x1f%(objectname)", "refs/remotes").splitlines():
        ref, sha = line.split("\x1f")
        if not ref.endswith("/HEAD"):
            refs.append((f"{ns}/remotes/{ref[len('refs/remotes/'):]}", sha))
    if state.worktree_tree:
        refs.append((f"{ns}/worktree", _tree_commit(repo, state.worktree_tree, f"gitrescue backup {bid}: worktree")))
    if state.index_tree:
        refs.append((f"{ns}/index", _tree_commit(repo, state.index_tree, f"gitrescue backup {bid}: index")))
    stdin = "".join(f"create {ref} {sha}\n" for ref, sha in refs)
    _g(repo, "update-ref", "--stdin", input_text=stdin)
    backup = Backup(bid, label, datetime.now(UTC).isoformat(timespec="seconds"), state)
    (_backup_dir(repo) / f"{bid}.json").write_text(json.dumps(backup.to_dict(), indent=2))
    return backup


def backup_exists(repo: Path, backup_id: str) -> bool:
    if (_backup_dir(repo) / f"{backup_id}.json").exists():
        return True
    return bool(_g(repo, "for-each-ref", "--count=1", "--format=%(refname)", f"{BACKUP_NS}/{backup_id}/").strip())


def load_backup(repo: Path, backup_id: str) -> Backup:
    if not _ID_RE.match(backup_id or ""):
        raise ValueError(f"invalid backup id {backup_id!r}")
    p = _backup_dir(repo) / f"{backup_id}.json"
    if not p.exists():
        raise ValueError(f"unknown backup id {backup_id!r}")
    d = json.loads(p.read_text())
    return Backup(d["id"], d["label"], d["created"], RepoState.from_dict(d["state"]))


def list_backups(repo: Path) -> list[Backup]:
    out = []
    for p in sorted(_backup_dir(repo).glob("*.json")):
        try:
            out.append(load_backup(repo, p.stem))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda b: b.created)


def backup_ref_tips(repo: Path) -> list[str]:
    out = _g(repo, "for-each-ref", "--format=%(objectname)", BACKUP_NS)
    return sorted(set(out.split()))


def _abort_in_progress(repo: Path) -> None:
    op = in_progress_operation(repo)
    if op == "rebase":
        if not run_git(repo, "rebase", "--abort", _guard=False).ok:
            run_git(repo, "rebase", "--quit", _guard=False)
    elif op == "merge":
        if not run_git(repo, "merge", "--abort", _guard=False).ok:
            gd = git_dir(repo)
            for f in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE"):
                (gd / f).unlink(missing_ok=True)
    elif op == "cherry-pick":
        if not run_git(repo, "cherry-pick", "--abort", _guard=False).ok:
            run_git(repo, "cherry-pick", "--quit", _guard=False)
    elif op == "revert":
        run_git(repo, "revert", "--abort", _guard=False)


def apply_state(repo: Path, target: RepoState) -> list[str]:
    """Make the repository match ``target``. Returns notes about what was done.

    Callers should create a safety backup first (``restore`` does).
    """
    repo = Path(repo)
    notes: list[str] = []
    _abort_in_progress(repo)
    current = capture_state(repo)

    # 1. Branch tips.
    lines = []
    for name, sha in target.branches.items():
        if current.branches.get(name) != sha:
            lines.append(f"update refs/heads/{name} {sha}\n")
    # Only drop branches whose commits are all contained in the restored state;
    # anything else keeps its name so no work becomes hard to find.
    keep_tips = list(target.branches.values()) + ([target.head_sha] if target.head_sha else [])
    for name, sha in current.branches.items():
        if name not in target.branches:
            if any(run_git(repo, "merge-base", "--is-ancestor", sha, t, _guard=False).ok for t in keep_tips):
                lines.append(f"delete refs/heads/{name} {sha}\n")
            else:
                notes.append(f"kept branch {name!r}: it has commits not in the restored state")
    if lines:
        _g(repo, "update-ref", "--stdin", input_text="".join(lines))

    # 2. HEAD.
    if target.head_ref and target.head_ref.removeprefix("refs/heads/") in target.branches:
        _g(repo, "symbolic-ref", "HEAD", target.head_ref)
    elif target.head_sha:
        _g(repo, "update-ref", "--no-deref", "HEAD", target.head_sha)

    # 3. Working tree and index.
    if target.worktree_tree:
        now = worktree_tree(repo)
        if now != target.worktree_tree:
            _g(repo, "read-tree", now)
            run_git(repo, "update-index", "-q", "--refresh", _guard=False)
            _g(repo, "read-tree", "-m", "-u", now, target.worktree_tree)
    if target.index_tree:
        _g(repo, "read-tree", target.index_tree)
    elif target.head_sha:
        _g(repo, "read-tree", target.head_sha)
    run_git(repo, "update-index", "-q", "--refresh", _guard=False)

    # 4. Stash list.
    if [s[0] for s in current.stash] != [s[0] for s in target.stash]:
        run_git(repo, "update-ref", "-d", "refs/stash", _guard=False)
        for sha, msg in reversed(target.stash):
            _g(repo, "stash", "store", "-m", msg or "restored by gitrescue", sha)
        notes.append("stash list restored")

    if target.in_progress:
        notes.append(f"the snapshot was taken during a {target.in_progress}; that operation was not resumed")
    return notes


def restore(repo: Path, backup_id: str, safety_prefix: str = "pre-restore") -> dict:
    """Restore a backup, after taking a safety backup of the current state."""
    backup = load_backup(repo, backup_id)
    safety = create_backup(repo, f"before restoring {backup_id}", new_backup_id(safety_prefix))
    notes = apply_state(repo, backup.state)
    return {"restored": backup_id, "safety_backup": safety.id, "notes": notes}
