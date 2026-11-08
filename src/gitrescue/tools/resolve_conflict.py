from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field

from gitrescue.git import git_ok, resolve_repo_path
from gitrescue.tools._common import conflicted_files
from gitrescue.tools.base import Preview, RiskTier, Tool, ToolInput, ToolResult

_MARKERS = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


class ResolveConflictInput(ToolInput):
    model_config = ToolInput.model_config | {"str_strip_whitespace": False}
    file: str = Field(description="Conflicted file, repo-relative.")
    content: str = Field(max_length=1_000_000, description="Full resolved file content (no conflict markers).")


def _rel(repo: Path, file: str) -> str:
    return str(resolve_repo_path(repo, file).relative_to(Path(repo).resolve()))


def _resolve(repo: Path, a: ResolveConflictInput) -> ToolResult:
    rel = _rel(repo, a.file)
    if rel not in conflicted_files(repo):
        return ToolResult.fail(f"{rel!r} is not a conflicted file", {"conflicted": conflicted_files(repo)})
    if _MARKERS.search(a.content):
        return ToolResult.fail("content still contains conflict markers")
    path = resolve_repo_path(repo, rel)
    path.write_text(a.content, encoding="utf-8")
    git_ok(repo, "add", "--", rel)
    return ToolResult.success({"resolved": rel, "remaining_conflicts": conflicted_files(repo)})


def _preview(repo: Path, a: ResolveConflictInput) -> Preview:
    rel = _rel(repo, a.file)
    lines = a.content.count("\n") + (0 if a.content.endswith("\n") or not a.content else 1)
    head = a.content[:600]
    return Preview(
        summary=f"overwrite conflicted file {rel} with resolved content ({lines} lines) and stage it",
        files_changed=[rel],
        warnings=[f"new content begins:\n{head}"],
    )


TOOL = Tool(
    name="resolve_conflict",
    description="Write the full resolved content of a conflicted file and stage it.",
    input_model=ResolveConflictInput,
    tier=RiskTier.DESTRUCTIVE,
    func=_resolve,
    preview=_preview,
)
