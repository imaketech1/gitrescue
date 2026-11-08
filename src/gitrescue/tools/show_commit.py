from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, truncate, validate_rev
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult

SEP = "\x1f"
MAX_PATCH_CHARS = 8_000


class ShowCommitInput(ToolInput):
    ref: str = Field("HEAD", description="Commit to show.")
    include_patch: bool = Field(True)


def _show(repo: Path, args: ShowCommitInput) -> ToolResult:
    sha = validate_rev(repo, args.ref)
    meta = git_ok(repo, "show", "-s", f"--format=%H{SEP}%P{SEP}%an{SEP}%ae{SEP}%aI{SEP}%B", "--end-of-options", sha)
    f = meta.split(SEP, 5)
    message, msg_trunc = truncate(f[5].strip(), 4000)
    numstat = git_ok(repo, "show", "--format=", "--numstat", "--no-color", "--end-of-options", sha)
    files = []
    for line in numstat.splitlines():
        a, d, name = (line.split("\t", 2) + ["", "", ""])[:3]
        files.append({"path": name, "added": None if a == "-" else int(a), "deleted": None if d == "-" else int(d)})
    data: dict = {
        "sha": f[0],
        "parents": f[1].split(),
        "author": f[2],
        "email": f[3],
        "date": f[4],
        "message": message,
        "files": files[:300],
        "file_count": len(files),
    }
    if msg_trunc:
        data["message_truncated"] = True
    if args.include_patch:
        patch = git_ok(repo, "show", "--format=", "--no-color", "--no-ext-diff", "--end-of-options", sha)
        data["patch"], data["patch_truncated"] = truncate(patch, MAX_PATCH_CHARS)
    return ToolResult.success(data)


TOOL = Tool(
    name="show_commit",
    description="Full details of one commit: message, parents, author, changed files and (truncated) patch.",
    input_model=ShowCommitInput,
    tier=RiskTier.READ,
    func=_show,
)
