from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, resolve_repo_path, truncate, validate_rev
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult

MAX_DIFF_CHARS = 12_000


class GitDiffInput(ToolInput):
    staged: bool = Field(False, description="Diff the index against HEAD instead of the working tree against the index.")
    from_ref: str | None = Field(None, description="Compare from this revision (overrides staged).")
    to_ref: str | None = Field(None, description="Compare to this revision (requires from_ref).")
    paths: list[str] = Field(default_factory=list, max_length=50)
    stat_only: bool = Field(False, description="Only per-file change counts, no patch text.")


def _diff(repo: Path, args: GitDiffInput) -> ToolResult:
    cmd = ["diff", "--no-color", "--no-ext-diff"]
    if args.from_ref:
        cmd += ["--end-of-options", validate_rev(repo, args.from_ref)]
        if args.to_ref:
            cmd.append(validate_rev(repo, args.to_ref))
    elif args.to_ref:
        raise ValueError("to_ref requires from_ref")
    elif args.staged:
        cmd.insert(1, "--cached")
    paths = [str(resolve_repo_path(repo, p).relative_to(Path(repo).resolve())) for p in args.paths]
    stat = git_ok(repo, *cmd[:1], "--numstat", *cmd[1:], "--", *paths)
    files = []
    for line in stat.splitlines():
        a, d, name = (line.split("\t", 2) + ["", "", ""])[:3]
        files.append({"path": name, "added": None if a == "-" else int(a), "deleted": None if d == "-" else int(d)})
    data: dict = {"files": files[:300], "file_count": len(files)}
    if not args.stat_only:
        patch = git_ok(repo, *cmd, "--", *paths)
        data["patch"], data["truncated"] = truncate(patch, MAX_DIFF_CHARS)
        if data["truncated"]:
            data["note"] = "Patch truncated. Narrow with paths=[...] or use stat_only=true."
    return ToolResult.success(data)


TOOL = Tool(
    name="git_diff",
    description="Show changes: working tree vs index (default), index vs HEAD (staged=true), or between two "
    "revisions. Large patches are truncated; use stat_only or paths to narrow.",
    input_model=GitDiffInput,
    tier=RiskTier.READ,
    func=_diff,
)
