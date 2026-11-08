from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import branch_exists, git_ok, run_git, validate_branch_name
from gitrescue.tools._common import commits_between
from gitrescue.tools.base import Preview, RiskTier, Tool, ToolInput, ToolResult


class PushForceWithLeaseInput(ToolInput):
    branch: str = Field(description="Local branch to push to the same name on the remote.")
    remote: str = Field("origin")


def remote_names(repo: Path) -> list[str]:
    return git_ok(repo, "remote").split()


def remote_tip(repo: Path, remote: str, branch: str) -> str | None:
    res = run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}")
    return res.stdout.strip() if res.ok else None


def _check(repo: Path, a: PushForceWithLeaseInput) -> None:
    validate_branch_name(repo, a.branch)
    if a.remote not in remote_names(repo):
        raise ValueError(f"unknown remote {a.remote!r}")
    if not branch_exists(repo, a.branch):
        raise ValueError(f"no local branch {a.branch!r}")


def _push(repo: Path, a: PushForceWithLeaseInput) -> ToolResult:
    _check(repo, a)
    expected = remote_tip(repo, a.remote, a.branch) or ""
    res = run_git(
        repo,
        "push",
        "--porcelain",
        f"--force-with-lease=refs/heads/{a.branch}:{expected}",
        a.remote,
        f"refs/heads/{a.branch}:refs/heads/{a.branch}",
        timeout=120,
    )
    if not res.ok:
        return ToolResult.fail("push rejected: " + (res.stderr or res.stdout).strip()[-800:])
    return ToolResult.success({"pushed": a.branch, "remote": a.remote, "previous_remote_tip": expected or None})


def _preview(repo: Path, a: PushForceWithLeaseInput) -> Preview:
    _check(repo, a)
    tip = remote_tip(repo, a.remote, a.branch)
    if tip is None:
        return Preview(summary=f"push new branch {a.branch} to {a.remote}")
    dropped = commits_between(repo, a.branch, tip)
    added = commits_between(repo, tip, a.branch)
    return Preview(
        summary=f"force-push {a.branch} to {a.remote}/{a.branch} (lease on {tip[:10]})",
        commits_moved=[f"removed from remote: {c}" for c in dropped] + [f"added to remote: {c}" for c in added],
        warnings=["rewrites the remote branch; collaborators must reset their copies"] if dropped else [],
    )


TOOL = Tool(
    name="push_force_with_lease",
    description="Force-push a local branch you own to the same-named remote branch, with an explicit lease so "
    "others' new work is never overwritten. Refused for main/master, the remote default branch, and branches "
    "with other authors' commits.",
    input_model=PushForceWithLeaseInput,
    tier=RiskTier.DESTRUCTIVE,
    func=_push,
    preview=_preview,
)
