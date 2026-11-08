from __future__ import annotations

from pathlib import Path

from gitrescue.git import git_ok
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult

SEP = "\x1f"


class ListBranchesInput(ToolInput):
    pass


def _branches(repo: Path, args: ListBranchesInput) -> ToolResult:
    fmt = SEP.join(["%(refname)", "%(objectname)", "%(HEAD)", "%(upstream:short)", "%(upstream:track)", "%(contents:subject)"])
    out = git_ok(repo, "for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes")
    local, remote = [], []
    for line in out.splitlines():
        f = line.split(SEP)
        if len(f) < 6:
            continue
        ref, sha, head, upstream, track, subject = f
        if ref.startswith("refs/heads/"):
            local.append(
                {
                    "name": ref[len("refs/heads/") :],
                    "sha": sha,
                    "current": head == "*",
                    "upstream": upstream or None,
                    "track": track or None,
                    "subject": subject[:200],
                }
            )
        elif not ref.endswith("/HEAD"):
            remote.append({"name": ref[len("refs/remotes/") :], "sha": sha, "subject": subject[:200]})
    return ToolResult.success({"local": local, "remote": remote})


TOOL = Tool(
    name="list_branches",
    description="Local and remote-tracking branches with tip sha, current marker, upstream and ahead/behind.",
    input_model=ListBranchesInput,
    tier=RiskTier.READ,
    func=_branches,
)
