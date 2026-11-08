from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import run_git
from gitrescue.tools._common import conflicted_files, head_summary
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class CommitInput(ToolInput):
    message: str = Field(min_length=1, max_length=5000)


def _commit(repo: Path, a: CommitInput) -> ToolResult:
    if conflicted_files(repo):
        return ToolResult.fail("cannot commit: there are unresolved conflicts")
    res = run_git(repo, "commit", "--no-edit", "--cleanup=strip", "-m", a.message)
    if not res.ok:
        return ToolResult.fail("commit failed: " + (res.stdout + res.stderr).strip()[-600:])
    return ToolResult.success(head_summary(repo))


TOOL = Tool(
    name="commit",
    description="Commit the staged changes with a message. Also concludes a merge once conflicts are resolved.",
    input_model=CommitInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_commit,
)
