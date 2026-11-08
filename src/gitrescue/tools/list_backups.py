from __future__ import annotations

from pathlib import Path

from gitrescue.policy.backup import list_backups
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class ListBackupsInput(ToolInput):
    pass


def _list(repo: Path, a: ListBackupsInput) -> ToolResult:
    return ToolResult.success(
        {"backups": [{"id": b.id, "label": b.label, "created": b.created} for b in list_backups(repo)[-30:]]}
    )


TOOL = Tool(
    name="list_backups",
    description="List GitRescue backups in this repository (newest last).",
    input_model=ListBackupsInput,
    tier=RiskTier.READ,
    func=_list,
)
