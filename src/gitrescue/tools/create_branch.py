from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import branch_exists, git_ok, validate_branch_name, validate_rev
from gitrescue.tools._common import head_summary
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class CreateBranchInput(ToolInput):
    name: str = Field(description="New local branch name.")
    start_point: str = Field("HEAD", description="Commit or branch the new branch points at.")
    checkout: bool = Field(False, description="Switch to the new branch after creating it.")


def _create(repo: Path, a: CreateBranchInput) -> ToolResult:
    validate_branch_name(repo, a.name)
    if branch_exists(repo, a.name):
        return ToolResult.fail(f"branch {a.name!r} already exists")
    sha = validate_rev(repo, a.start_point)
    git_ok(repo, "branch", "--no-track", "--end-of-options", a.name, sha)
    if a.checkout:
        git_ok(repo, "switch", "--no-guess", a.name)
    return ToolResult.success({"created": a.name, "at": sha, **head_summary(repo)})


TOOL = Tool(
    name="create_branch",
    description="Create a local branch at a commit (optionally switch to it). Never moves existing branches.",
    input_model=CreateBranchInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_create,
)
