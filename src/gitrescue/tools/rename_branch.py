from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import branch_exists, git_ok, validate_branch_name
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class RenameBranchInput(ToolInput):
    old: str = Field(description="Existing local branch.")
    new: str = Field(description="New name.")


def _rename(repo: Path, a: RenameBranchInput) -> ToolResult:
    validate_branch_name(repo, a.old)
    validate_branch_name(repo, a.new)
    if not branch_exists(repo, a.old):
        return ToolResult.fail(f"no local branch {a.old!r}")
    if branch_exists(repo, a.new):
        return ToolResult.fail(f"branch {a.new!r} already exists")
    git_ok(repo, "branch", "-m", "--end-of-options", a.old, a.new)
    return ToolResult.success({"renamed": a.old, "to": a.new, "note": "remote branches are not renamed"})


TOOL = Tool(
    name="rename_branch",
    description="Rename a local branch (refuses to overwrite an existing branch). Does not touch remotes.",
    input_model=RenameBranchInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_rename,
)
