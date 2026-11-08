from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from gitrescue.git import head_sha, in_progress_operation, run_git, validate_rev
from gitrescue.tools._common import commits_between, conflicted_files, head_summary, op_failed
from gitrescue.tools.base import Preview, RiskTier, Tool, ToolInput, ToolResult


class RebaseInput(ToolInput):
    action: Literal["start", "continue", "abort", "skip"] = "start"
    upstream: str | None = Field(None, description="For start: replay commits not in upstream on top of it.")
    onto: str | None = Field(None, description="Optional --onto target.")


def _rebase(repo: Path, a: RebaseInput) -> ToolResult:
    if a.action == "start":
        if not a.upstream:
            raise ValueError("upstream is required for action=start")
        if in_progress_operation(repo):
            return ToolResult.fail(f"a {in_progress_operation(repo)} is already in progress")
        cmd = ["rebase", "--no-autostash"]
        if a.onto:
            cmd += ["--onto", validate_rev(repo, a.onto)]
        cmd += ["--end-of-options", validate_rev(repo, a.upstream)]
        res = run_git(repo, *cmd)
    else:
        if in_progress_operation(repo) != "rebase":
            return ToolResult.fail("no rebase in progress")
        if a.action == "continue" and conflicted_files(repo):
            return ToolResult.fail("resolve all conflicts first", {"conflicted": conflicted_files(repo)})
        res = run_git(repo, "rebase", f"--{a.action}")
    if not res.ok:
        return op_failed(repo, f"rebase {a.action}", res)
    return ToolResult.success({"action": a.action, "in_progress": in_progress_operation(repo), **head_summary(repo)})


def _preview(repo: Path, a: RebaseInput) -> Preview:
    if a.action != "start":
        warn = ["skip drops the current commit from the rebased branch"] if a.action == "skip" else []
        return Preview(summary=f"rebase --{a.action}", warnings=warn)
    up = validate_rev(repo, a.upstream or "HEAD")
    head = head_sha(repo) or up
    onto = validate_rev(repo, a.onto) if a.onto else up
    moved = commits_between(repo, up, head)
    return Preview(
        summary=f"rebase {len(moved)} commit(s) of the current branch onto {onto[:10]}",
        commits_moved=[f"rewritten: {c}" for c in moved],
        warnings=["rewrites history of the current branch; already-pushed commits will need a force push"],
    )


TOOL = Tool(
    name="rebase",
    description="Non-interactive rebase: start (replay current branch onto upstream / --onto), or "
    "continue/abort/skip an in-progress rebase.",
    input_model=RebaseInput,
    tier=RiskTier.DESTRUCTIVE,
    func=_rebase,
    preview=_preview,
)
