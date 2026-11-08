from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import branch_exists, run_git, validate_rev
from gitrescue.tools._common import head_summary
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class CheckoutInput(ToolInput):
    target: str = Field(description="Local branch name to switch to, or a commit to detach at.")
    detach: bool = Field(False, description="Detach HEAD at the commit even if target is a branch.")


def _checkout(repo: Path, a: CheckoutInput) -> ToolResult:
    if not a.detach and not a.target.startswith("-") and branch_exists(repo, a.target):
        res = run_git(repo, "switch", "--no-guess", a.target)
    else:
        sha = validate_rev(repo, a.target)
        res = run_git(repo, "switch", "--detach", sha)
    if not res.ok:
        return ToolResult.fail(
            "checkout refused by git (it never discards local changes): " + (res.stderr or res.stdout).strip()[-800:]
        )
    return ToolResult.success(head_summary(repo))


TOOL = Tool(
    name="checkout",
    description="Switch to a local branch or detach at a commit. Refuses if it would overwrite uncommitted changes.",
    input_model=CheckoutInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_checkout,
)
