from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, validate_rev
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult

SEP = "\x1f"
FMT = SEP.join(["%H", "%h", "%P", "%an", "%ae", "%aI", "%D", "%s"])


class GitLogInput(ToolInput):
    ref: str = Field("HEAD", description="Revision to start from (branch, sha, HEAD~n).")
    max_count: int = Field(20, ge=1, le=100)
    all_refs: bool = Field(False, description="Show commits from all branches, not just ref.")
    exclude_ref: str | None = Field(None, description="Hide commits reachable from this revision (ref..exclude).")


def parse_log(out: str) -> list[dict]:
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split(SEP)
        if len(f) < 8:
            continue
        commits.append(
            {
                "sha": f[0],
                "short": f[1],
                "parents": f[2].split() if f[2] else [],
                "author": f[3],
                "email": f[4],
                "date": f[5],
                "refs": [r.strip() for r in f[6].split(",") if r.strip()],
                "subject": f[7][:300],
            }
        )
    return commits


def _log(repo: Path, args: GitLogInput) -> ToolResult:
    cmd = ["log", f"--max-count={args.max_count}", f"--format={FMT}", "--end-of-options"]
    if args.all_refs:
        cmd = ["log", f"--max-count={args.max_count}", f"--format={FMT}", "--branches", "--remotes", "--tags", "HEAD"]
    else:
        cmd.append(validate_rev(repo, args.ref))
        if args.exclude_ref:
            cmd.append("^" + validate_rev(repo, args.exclude_ref))
    return ToolResult.success({"commits": parse_log(git_ok(repo, *cmd))})


TOOL = Tool(
    name="git_log",
    description="List commits (newest first) with sha, parents, author, date, ref decorations and subject.",
    input_model=GitLogInput,
    tier=RiskTier.READ,
    func=_log,
)
