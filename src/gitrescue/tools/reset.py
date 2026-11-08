from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from gitrescue.git import git_ok, head_sha, validate_rev
from gitrescue.tools._common import changed_files, commits_between, head_summary, uncommitted_files
from gitrescue.tools.base import Preview, RiskTier, Tool, ToolInput, ToolResult


class ResetInput(ToolInput):
    mode: Literal["soft", "mixed", "hard"] = Field(
        description="soft: keep changes staged; mixed: keep changes unstaged; hard: discard changes."
    )
    target: str = Field(description="Commit to move the current branch (HEAD) to, e.g. HEAD~1 or a sha.")


def _reset(repo: Path, a: ResetInput) -> ToolResult:
    sha = validate_rev(repo, a.target)
    before = head_sha(repo)
    git_ok(repo, "reset", "-q", f"--{a.mode}", sha)  # sha is a validated full object id
    return ToolResult.success({"mode": a.mode, "from": before, **head_summary(repo)})


def _preview(repo: Path, a: ResetInput) -> Preview:
    sha = validate_rev(repo, a.target)
    head = head_sha(repo) or sha
    moved = commits_between(repo, sha, head)
    warnings = []
    if a.mode == "hard":
        dirty = uncommitted_files(repo)
        if dirty:
            warnings.append(f"--hard discards uncommitted changes in {len(dirty)} file(s) (kept in the backup)")
    files = changed_files(repo, sha, head)
    if a.mode == "hard":
        files = files + [f"(uncommitted) {f}" for f in uncommitted_files(repo)]
    ahead = git_ok(repo, "rev-list", "--count", "--end-of-options", f"{head}..{sha}").strip()
    if ahead != "0":
        warnings.append(f"moves the branch forward/sideways onto {ahead} commit(s) not currently on it")
    return Preview(
        summary=f"reset --{a.mode} current branch from {head[:10]} to {sha[:10]}",
        commits_moved=[f"leaves branch: {c}" for c in moved],
        files_changed=files,
        warnings=warnings,
    )


TOOL = Tool(
    name="reset",
    description="Move the current branch to another commit (git reset --soft/--mixed/--hard).",
    input_model=ResetInput,
    tier=RiskTier.DESTRUCTIVE,
    func=_reset,
    preview=_preview,
)
