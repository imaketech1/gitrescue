"""Low-level git runner.

Every git invocation in GitRescue goes through :func:`run_git`. It never uses a
shell, never prompts, and (unless ``_guard=False`` is passed by internal
infrastructure code) refuses a fixed set of dangerous git operations no matter
who asked for them. Tool modules must never pass ``_guard=False``; a test
enforces that.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROTECTED_BRANCHES = frozenset({"main", "master"})

_SAFE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
    "GIT_SEQUENCE_EDITOR": "true",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LC_ALL": "C",
    "GIT_ASKPASS": "true",
    "SSH_ASKPASS": "true",
    "GIT_MERGE_AUTOEDIT": "no",
}

# Repository hooks are code from the (untrusted) repository: never run them.
_NO_HOOKS_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.hooksPath",
    "GIT_CONFIG_VALUE_0": os.devnull,
}

_CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
_FORBIDDEN_SUBCOMMANDS = {
    "clean": "git clean is blocked: it deletes untracked files irrecoverably",
    "credential": "credential access is blocked",
    "credential-cache": "credential access is blocked",
    "credential-store": "credential access is blocked",
    "filter-branch": "history filtering is blocked",
    "filter-repo": "history filtering is blocked",
    "gc": "garbage collection is blocked (it can prune recoverable commits)",
    "prune": "pruning is blocked (it can delete recoverable commits)",
    "reflog": None,  # allowed only for reading; checked below
    "remote": None,  # allowed only for reading; checked below
}


class GitBlocked(Exception):
    """Raised when a git invocation is refused by the low-level guard."""


@dataclass
class GitResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    if not args:
        return None, []
    return args[0], args[1:]


def guard_git_args(args: list[str]) -> None:
    """Raise GitBlocked if the argv is something GitRescue must never run."""
    if any(not isinstance(a, str) for a in args):
        raise GitBlocked("git arguments must be strings")
    if any("\x00" in a or "\n" in a for a in args):
        raise GitBlocked("control characters in git arguments")
    sub, rest = _subcommand(args)
    if sub is None:
        raise GitBlocked("empty git command")
    if sub.startswith("-"):
        # global options such as -c core.sshCommand=... or --git-dir=...
        raise GitBlocked(f"global git option {sub!r} is not allowed")
    reason = _FORBIDDEN_SUBCOMMANDS.get(sub, "")
    if reason:
        raise GitBlocked(reason)
    if sub == "config":
        if not any(a in _CONFIG_READ_FLAGS for a in rest):
            raise GitBlocked("git config changes are blocked")
        if any(a in {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e"} for a in rest):
            raise GitBlocked("git config changes are blocked")
    if sub == "reflog" and rest and rest[0] in {"expire", "delete"}:
        raise GitBlocked("rewriting the reflog is blocked")
    if sub == "remote" and rest and rest[0] not in {"-v", "show", "get-url"}:
        raise GitBlocked("changing remotes is blocked")
    if sub == "branch" and any(a in {"-D", "-d", "--delete"} for a in rest) and any(
        a in {"-r", "--remotes"} for a in rest
    ):
        raise GitBlocked("deleting remote-tracking branches is blocked")
    if sub == "update-ref" and any(a.startswith("refs/remotes/") for a in rest):
        raise GitBlocked("editing remote-tracking refs is blocked")
    if sub == "push":
        _guard_push(rest)


def _guard_push(rest: list[str]) -> None:
    flags = [a for a in rest if a.startswith("-")]
    positional = [a for a in rest if not a.startswith("-")]
    banned = {"--delete", "-d", "--mirror", "--all", "--prune", "--tags", "--force", "-f", "--no-verify"}
    for f in flags:
        base = f.split("=", 1)[0]
        if base in banned:
            raise GitBlocked(f"push flag {f!r} is blocked")
        if base not in {"--force-with-lease", "--porcelain", "--quiet", "-q", "--dry-run", "-n"}:
            raise GitBlocked(f"push flag {f!r} is not allowed")
    for refspec in positional[1:]:
        if refspec.startswith("+"):
            raise GitBlocked("forced refspecs ('+src:dst') are blocked; use --force-with-lease")
        if refspec.startswith(":"):
            raise GitBlocked("deleting remote branches is blocked")
        dst = refspec.split(":", 1)[-1]
        name = re.sub(r"^refs/heads/", "", dst)
        if name in PROTECTED_BRANCHES and any(f.startswith("--force-with-lease") for f in flags):
            raise GitBlocked(f"force-pushing to protected branch {name!r} is blocked")
    if any(f.startswith("--force-with-lease") for f in flags) and len(positional) < 2:
        raise GitBlocked("force pushes must name an explicit branch")


def run_git(
    repo: Path | str,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 60,
    input_text: str | None = None,
    _guard: bool = True,
) -> GitResult:
    argv = list(args)
    if _guard:
        guard_git_args(argv)
    full_env = dict(os.environ)
    # Never let the caller's environment redirect git elsewhere or inject config.
    for k in list(full_env):
        if k in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_OBJECT_DIRECTORY"} or k.startswith(
            "GIT_CONFIG_"
        ):
            full_env.pop(k)
    full_env.update(_SAFE_ENV)
    full_env.update(_NO_HOOKS_ENV)
    full_env.update(env or {})
    proc = subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
        timeout=timeout,
        input=input_text,
        shell=False,
    )
    return GitResult(argv, proc.returncode, proc.stdout, proc.stderr)


def git_ok(repo: Path | str, *args: str, _guard: bool = True, **kw) -> str:
    """Run git and return stdout, raising GitError on failure."""
    res = run_git(repo, *args, _guard=_guard, **kw)
    if not res.ok:
        raise GitError(res)
    return res.stdout


class GitError(Exception):
    def __init__(self, result: GitResult):
        self.result = result
        msg = (result.stderr or result.stdout).strip() or f"git exited {result.returncode}"
        super().__init__(msg)


def repo_root(path: Path | str) -> Path:
    out = git_ok(path, "rev-parse", "--show-toplevel").strip()
    return Path(out).resolve()


def git_dir(repo: Path | str) -> Path:
    out = git_ok(repo, "rev-parse", "--absolute-git-dir").strip()
    return Path(out)


# ---------------------------------------------------------------------------
# Validation helpers shared by tools and policy
# ---------------------------------------------------------------------------

_REF_BAD = re.compile(r"[\s\x00-\x1f\x7f~^:?*\[\\]|\.\.|@\{")


def validate_rev(repo: Path, rev: str) -> str:
    """Resolve a user/model supplied revision to a full commit sha.

    Accepts ``HEAD~2``, ``main``, ``abc123``, ``stash@{0}``, ``HEAD@{3}``.
    Rejects anything that could be parsed as an option.
    """
    if not rev or rev.startswith("-") or len(rev) > 200:
        raise ValueError(f"invalid revision: {rev!r}")
    if any(c in rev for c in "\x00\n\r\t "):
        raise ValueError(f"invalid revision: {rev!r}")
    res = run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    if not res.ok:
        raise ValueError(f"unknown revision: {rev!r}")
    return res.stdout.strip()


def validate_branch_name(repo: Path, name: str) -> str:
    if not name or name.startswith("-") or len(name) > 200 or _REF_BAD.search(name):
        raise ValueError(f"invalid branch name: {name!r}")
    res = run_git(repo, "check-ref-format", "--branch", name)
    if not res.ok:
        raise ValueError(f"invalid branch name: {name!r}")
    return name


def branch_exists(repo: Path, name: str) -> bool:
    return run_git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}").ok


def resolve_repo_path(repo: Path, path: str) -> Path:
    """Resolve ``path`` relative to the repo and ensure it stays inside it (and out of .git)."""
    if not path or "\x00" in path:
        raise ValueError("empty or invalid path")
    root = Path(repo).resolve()
    p = Path(path)
    candidate = (p if p.is_absolute() else root / p).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"path {path!r} is outside the repository") from None
    if rel.parts and rel.parts[0] == ".git":
        raise ValueError(f"path {path!r} is inside .git")
    if str(rel) == ".":
        raise ValueError("path must name a file inside the repository")
    return candidate


def current_branch(repo: Path) -> str | None:
    res = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    return res.stdout.strip() if res.ok else None


def head_sha(repo: Path) -> str | None:
    res = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    return res.stdout.strip() if res.ok else None


def in_progress_operation(repo: Path) -> str | None:
    gd = git_dir(repo)
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        return "rebase"
    if (gd / "MERGE_HEAD").exists():
        return "merge"
    if (gd / "CHERRY_PICK_HEAD").exists():
        return "cherry-pick"
    if (gd / "REVERT_HEAD").exists():
        return "revert"
    return None


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]", True
