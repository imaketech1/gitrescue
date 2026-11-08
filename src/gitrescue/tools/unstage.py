from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, head_sha, resolve_repo_path
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class UnstageInput(ToolInput):
    paths: list[str] = Field(default_factory=list, max_length=200, description="Files to unstage; empty = all.")


def _unstage(repo: Path, a: UnstageInput) -> ToolResult:
    root = Path(repo).resolve()
    rel = [str(resolve_repo_path(repo, p).relative_to(root)) for p in a.paths] or ["."]
    if head_sha(repo) is None:
        git_ok(repo, "rm", "--cached", "-r", "-q", "--", *rel)
    else:
        git_ok(repo, "restore", "--staged", "--", *rel)
    staged = git_ok(repo, "diff", "--cached", "--name-only").split()
    return ToolResult.success({"still_staged": staged[:200]})


TOOL = Tool(
    name="unstage",
    description="Remove files from the index without touching the working tree (git restore --staged).",
    input_model=UnstageInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_unstage,
)
