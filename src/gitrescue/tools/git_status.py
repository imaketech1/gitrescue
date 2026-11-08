from __future__ import annotations

from pathlib import Path

from gitrescue.git import git_ok, in_progress_operation, run_git
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class GitStatusInput(ToolInput):
    pass


def _status(repo: Path, args: GitStatusInput) -> ToolResult:
    out = git_ok(repo, "status", "--porcelain=v2", "--branch", "--untracked-files=all")
    data: dict = {
        "branch": None,
        "detached": False,
        "head": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "staged": [],
        "unstaged": [],
        "untracked": [],
        "conflicted": [],
    }
    for line in out.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
            data["head"] = None if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            name = line.split(" ", 2)[2]
            if name == "(detached)":
                data["detached"] = True
            else:
                data["branch"] = name
        elif line.startswith("# branch.upstream "):
            data["upstream"] = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            _, _, a, b = line.split(" ")
            data["ahead"], data["behind"] = int(a), -int(b)
        elif line.startswith("1 ") or line.startswith("2 "):
            parts = line.split(" ", 8 if line[0] == "1" else 9)
            xy = parts[1]
            path = parts[-1]
            if line[0] == "2":
                path = path.split("\t")[0]
            if xy[0] != ".":
                data["staged"].append({"path": path, "status": xy[0]})
            if xy[1] != ".":
                data["unstaged"].append({"path": path, "status": xy[1]})
        elif line.startswith("u "):
            data["conflicted"].append(line.split(" ", 10)[-1])
        elif line.startswith("? "):
            data["untracked"].append(line[2:])
    data["in_progress"] = in_progress_operation(repo)
    stash = run_git(repo, "stash", "list")
    data["stash_count"] = len(stash.stdout.splitlines()) if stash.ok else 0
    for key in ("staged", "unstaged", "untracked", "conflicted"):
        if len(data[key]) > 200:
            data[f"{key}_total"] = len(data[key])
            data[key] = data[key][:200]
    return ToolResult.success(data)


TOOL = Tool(
    name="git_status",
    description="Current branch, upstream, ahead/behind, staged/unstaged/untracked/conflicted files, "
    "any in-progress merge/rebase/cherry-pick, and stash count.",
    input_model=GitStatusInput,
    tier=RiskTier.READ,
    func=_status,
)
