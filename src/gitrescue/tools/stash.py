from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from gitrescue.git import git_ok, run_git
from gitrescue.tools._common import op_failed
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class StashInput(ToolInput):
    action: Literal["push", "apply", "pop", "list"]
    message: str | None = Field(None, max_length=200)
    include_untracked: bool = False
    index: int = Field(0, ge=0, le=100, description="Stash entry for apply/pop (stash@{index}).")
    restore_index: bool = Field(True, description="For apply/pop: also restore what was staged.")


def _stash(repo: Path, a: StashInput) -> ToolResult:
    if a.action == "list":
        out = git_ok(repo, "stash", "list", "--format=%gd\x1f%H\x1f%gs")
        return ToolResult.success(
            {"entries": [dict(zip(("ref", "sha", "message"), ln.split("\x1f"), strict=False)) for ln in out.splitlines()]}
        )
    if a.action == "push":
        cmd = ["stash", "push"]
        if a.include_untracked:
            cmd.append("--include-untracked")
        if a.message:
            cmd += ["-m", a.message]
        res = run_git(repo, *cmd)
        if not res.ok:
            return ToolResult.fail("stash push failed: " + res.stderr.strip()[-500:])
        if "No local changes" in res.stdout:
            return ToolResult.fail("nothing to stash")
        return ToolResult.success({"stashed": True})
    cmd = ["stash", a.action]
    if a.restore_index:
        cmd.append("--index")
    cmd.append(f"stash@{{{a.index}}}")
    res = run_git(repo, *cmd)
    if not res.ok:
        return op_failed(repo, f"stash {a.action}", res)
    return ToolResult.success({"action": a.action, "entry": a.index})


TOOL = Tool(
    name="stash",
    description="Stash uncommitted changes (push), re-apply them (apply/pop) or list stash entries. "
    "pop keeps the entry if applying conflicts.",
    input_model=StashInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_stash,
)
