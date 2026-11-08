from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, resolve_repo_path
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class StageInput(ToolInput):
    paths: list[str] = Field(default_factory=list, max_length=200, description="Files to stage (repo-relative).")
    all: bool = Field(False, description="Stage every change, including deletions and untracked files.")


def _stage(repo: Path, a: StageInput) -> ToolResult:
    root = Path(repo).resolve()
    if a.all:
        git_ok(repo, "add", "-A")
    elif a.paths:
        rel = [str(resolve_repo_path(repo, p).relative_to(root)) for p in a.paths]
        git_ok(repo, "add", "--", *rel)
    else:
        raise ValueError("give paths or all=true")
    staged = git_ok(repo, "diff", "--cached", "--name-only").split()
    return ToolResult.success({"staged": staged[:200]})


TOOL = Tool(
    name="stage",
    description="Stage files for the next commit (git add).",
    input_model=StageInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_stage,
)
