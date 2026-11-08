from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, validate_branch_name
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult

SEP = "\x1f"


class GitReflogInput(ToolInput):
    ref: str = Field("HEAD", description="HEAD or a local branch name.")
    max_count: int = Field(30, ge=1, le=200)


def _reflog(repo: Path, args: GitReflogInput) -> ToolResult:
    ref = args.ref
    if ref != "HEAD":
        validate_branch_name(repo, ref)
    out = git_ok(
        repo, "reflog", "show", f"--max-count={args.max_count}", f"--format=%H{SEP}%gd{SEP}%gs{SEP}%cI", "--end-of-options", ref
    )
    entries = []
    for line in out.splitlines():
        f = line.split(SEP)
        if len(f) >= 4:
            entries.append({"sha": f[0], "selector": f[1], "action": f[2][:300], "date": f[3]})
    return ToolResult.success({"entries": entries})


TOOL = Tool(
    name="git_reflog",
    description="Reflog entries (where HEAD or a branch has pointed recently). Use it to find commits lost "
    "after reset, rebase, or detached-HEAD work.",
    input_model=GitReflogInput,
    tier=RiskTier.READ,
    func=_reflog,
)
