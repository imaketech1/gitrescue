"""Sandbox mode: run the agent on a throwaway copy, then apply the result.

The copy is a full directory copy (not ``git clone``) so the reflog, stash,
uncommitted changes and untracked files are all there for recovery work.
Pushes from the sandbox are deferred and replayed against the real repository
only after the user approves the final plan, through the real policy engine.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gitrescue.git import git_ok, in_progress_operation, run_git
from gitrescue.policy import backup as backups
from gitrescue.policy.backup import RepoState, capture_state

PUSH_DISABLED_URL = "/nonexistent/gitrescue-sandbox-push-disabled"


class SandboxError(Exception):
    pass


@dataclass
class BranchChange:
    name: str
    kind: str  # created | deleted | moved
    old: str | None
    new: str | None
    commits_added: list[str] = field(default_factory=list)
    commits_removed: list[str] = field(default_factory=list)


@dataclass
class SandboxPlan:
    branches: list[BranchChange]
    head_before: str
    head_after: str
    files: list[str]
    stash_before: int
    stash_after: int
    pushes: list[dict[str, Any]]
    blocking_issue: str | None = None

    @property
    def empty(self) -> bool:
        return (
            not self.branches
            and self.head_before == self.head_after
            and not self.files
            and self.stash_before == self.stash_after
            and not self.pushes
        )


def _g(repo: Path, *args: str, **kw) -> str:
    return git_ok(repo, *args, _guard=False, **kw)


def _describe_head(state: RepoState) -> str:
    if state.head_ref:
        return f"{state.head_ref.removeprefix('refs/heads/')} @ {(state.head_sha or '')[:10]}"
    return f"detached @ {(state.head_sha or '')[:10]}"


class Sandbox:
    def __init__(self, real_repo: Path):
        self.real = Path(real_repo).resolve()
        gd = self.real / ".git"
        if not gd.is_dir():
            raise SandboxError("sandbox mode needs a regular repository (.git directory); use --no-sandbox")
        self._tmp = Path(tempfile.mkdtemp(prefix="gitrescue-sandbox-"))
        self.path = self._tmp / self.real.name
        self.start_state = capture_state(self.real)
        shutil.copytree(self.real, self.path, symlinks=True)
        # Locks copied mid-operation would wedge the sandbox.
        for lock in (self.path / ".git").glob("*.lock"):
            lock.unlink()
        self._isolate_remotes()
        self.deferred_pushes: list[dict[str, Any]] = []

    # -- setup -----------------------------------------------------------------------------

    def _isolate_remotes(self) -> None:
        for name in _g(self.path, "remote").split():
            url = run_git(self.path, "remote", "get-url", name).stdout.strip()
            if url and "://" not in url and ":" not in url.split("/")[0]:
                p = Path(url)
                if not p.is_absolute():
                    _g(self.path, "remote", "set-url", name, str((self.real / p).resolve()))
            _g(self.path, "config", f"remote.{name}.pushurl", PUSH_DISABLED_URL)

    # -- planning --------------------------------------------------------------------------

    def final_state(self) -> RepoState:
        return capture_state(self.path)

    def plan(self) -> SandboxPlan:
        start, end = self.start_state, self.final_state()
        changes: list[BranchChange] = []
        for name in sorted(set(start.branches) | set(end.branches)):
            old, new = start.branches.get(name), end.branches.get(name)
            if old == new:
                continue
            kind = "created" if old is None else "deleted" if new is None else "moved"
            ch = BranchChange(name, kind, old, new)
            if new:
                ch.commits_added = self._log(new, old)
            if old:
                ch.commits_removed = self._log(old, new)
            changes.append(ch)
        files: list[str] = []
        if start.worktree_tree and end.worktree_tree and start.worktree_tree != end.worktree_tree:
            files = _g(self.path, "diff", "--name-status", "--no-renames", start.worktree_tree, end.worktree_tree).splitlines()
        issue = None
        op = in_progress_operation(self.path)
        if op:
            issue = f"the sandbox is in the middle of a {op}; finish or abort it before applying"
        elif end.index_tree is None:
            issue = "the sandbox index has unresolved conflicts"
        return SandboxPlan(
            branches=changes,
            head_before=_describe_head(start),
            head_after=_describe_head(end),
            files=files,
            stash_before=len(start.stash),
            stash_after=len(end.stash),
            pushes=list(self.deferred_pushes),
            blocking_issue=issue,
        )

    def _log(self, include: str, exclude: str | None, limit: int = 30) -> list[str]:
        args = ["log", f"--max-count={limit}", "--format=%h %s", include]
        if exclude:
            args.append(f"^{exclude}")
        return _g(self.path, *args).splitlines()

    # -- applying --------------------------------------------------------------------------

    def apply(self, run_id: str, push_executor=None) -> dict[str, Any]:
        """Apply the sandbox's final state to the real repository.

        ``push_executor(args) -> Execution`` replays deferred pushes (normally a
        real-repo PolicyEngine's ``execute`` bound to push_force_with_lease).
        """
        plan = self.plan()
        if plan.blocking_issue:
            raise SandboxError(plan.blocking_issue)
        now = capture_state(self.real)
        if not _same(now, self.start_state):
            raise SandboxError("the repository changed while the agent was running; nothing was applied")
        final = self.final_state()

        # 1. Back up the real repository.
        real_backup = backups.create_backup(self.real, f"before applying sandbox run {run_id}", backups.new_backup_id(f"apply-{run_id}"))

        # 2. Pin the sandbox's final state and bring all of its objects over.
        sb_id = backups.new_backup_id("final")
        backups.create_backup(self.path, "sandbox final state", sb_id, final)
        staging = f"refs/gitrescue/sandbox/{sb_id}"
        _g(
            self.real,
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            str(self.path),
            f"{backups.BACKUP_NS}/{sb_id}/*:{staging}/*",
        )
        try:
            # 3. Compare-and-swap branch updates, HEAD, index, worktree, stash.
            notes = backups.apply_state(self.real, final)
        except Exception as e:
            backups.apply_state(self.real, real_backup.state)
            raise SandboxError(f"apply failed and the repository was restored: {e}") from e
        finally:
            for ref in _g(self.real, "for-each-ref", "--format=%(refname)", staging).split():
                run_git(self.real, "update-ref", "-d", ref, _guard=False)

        # 4. Deferred pushes go through the real policy engine (hard blocks + approval) again.
        pushes = []
        for args in self.deferred_pushes:
            if push_executor is None:
                pushes.append({"args": args, "ok": False, "error": "no push executor; push skipped"})
                continue
            ex = push_executor(args)
            pushes.append({"args": args, "ok": ex.result.ok, "error": ex.result.error, "approval": ex.approval})
        return {"backup_id": real_backup.id, "notes": notes, "pushes": pushes}

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def _same(a: RepoState, b: RepoState) -> bool:
    return (
        a.head_ref == b.head_ref
        and a.head_sha == b.head_sha
        and a.branches == b.branches
        and [s[0] for s in a.stash] == [s[0] for s in b.stash]
        and a.worktree_tree == b.worktree_tree
        and a.index_tree == b.index_tree
    )


__all__ = ["Sandbox", "SandboxError", "SandboxPlan"]
