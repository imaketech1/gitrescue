from __future__ import annotations

from pathlib import Path

from gitrescue.git import git_ok, in_progress_operation, run_git
from gitrescue.tools.base import ToolResult


def conflicted_files(repo: Path) -> list[str]:
    out = run_git(repo, "diff", "--name-only", "--diff-filter=U")
    return sorted(set(out.stdout.split("\n")) - {""}) if out.ok else []


def head_summary(repo: Path) -> dict:
    sym = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    head = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    return {"branch": sym.stdout.strip() if sym.ok else None, "head": head.stdout.strip() if head.ok else None}


def op_failed(repo: Path, what: str, res) -> ToolResult:
    """Structured failure for an operation that may have stopped on conflicts."""
    conflicts = conflicted_files(repo)
    data = {**head_summary(repo), "in_progress": in_progress_operation(repo), "conflicted": conflicts}
    msg = (res.stderr or res.stdout).strip()[-1500:]
    if conflicts:
        data["hint"] = "Resolve each conflicted file with resolve_conflict, then continue (or abort)."
        return ToolResult.fail(f"{what} stopped with conflicts in {len(conflicts)} file(s)", data)
    return ToolResult.fail(f"{what} failed: {msg}", data)


def commits_between(repo: Path, exclude: str, include: str, limit: int = 50) -> list[str]:
    out = git_ok(repo, "log", f"--max-count={limit}", "--format=%h %s", "--end-of-options", include, f"^{exclude}")
    return [line[:160] for line in out.splitlines()]


def changed_files(repo: Path, a: str, b: str, limit: int = 100) -> list[str]:
    out = git_ok(repo, "diff", "--name-status", "--no-renames", "--end-of-options", a, b)
    lines = out.splitlines()
    return lines[:limit] + ([f"... and {len(lines) - limit} more"] if len(lines) > limit else [])


def uncommitted_files(repo: Path, limit: int = 100) -> list[str]:
    out = git_ok(repo, "status", "--porcelain=v1", "--untracked-files=no")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[:limit] + ([f"... and {len(lines) - limit} more"] if len(lines) > limit else [])
