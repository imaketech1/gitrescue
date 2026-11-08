from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from gitrescue.git import in_progress_operation, run_git, validate_rev
from gitrescue.tools._common import head_summary, op_failed
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class CherryPickInput(ToolInput):
    commits: list[str] = Field(default_factory=list, max_length=50, description="Commits to apply, oldest first.")
    action: Literal["pick", "continue", "abort"] = "pick"


def _cherry_pick(repo: Path, a: CherryPickInput) -> ToolResult:
    if a.action == "pick":
        if not a.commits:
            raise ValueError("commits is required for action=pick")
        shas = [validate_rev(repo, c) for c in a.commits]
        res = run_git(repo, "cherry-pick", "--allow-empty", "--end-of-options", *shas)
    else:
        if in_progress_operation(repo) != "cherry-pick":
            return ToolResult.fail("no cherry-pick in progress")
        res = run_git(repo, "cherry-pick", f"--{a.action}")
    if not res.ok:
        return op_failed(repo, f"cherry-pick {a.action}", res)
    return ToolResult.success({"action": a.action, **head_summary(repo)})


TOOL = Tool(
    name="cherry_pick",
    description="Apply existing commits onto the current branch (action=pick), or continue/abort an in-progress "
    "cherry-pick. On conflict, returns the conflicted files.",
    input_model=CherryPickInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_cherry_pick,
)
