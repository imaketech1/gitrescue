from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.policy.backup import create_backup
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class CreateBackupInput(ToolInput):
    label: str = Field("manual backup", max_length=200)


def _backup(repo: Path, a: CreateBackupInput) -> ToolResult:
    b = create_backup(repo, a.label)
    return ToolResult.success({"backup_id": b.id, "branches": len(b.state.branches)})


TOOL = Tool(
    name="create_backup_ref",
    description="Snapshot all branches, HEAD, stash, index and working tree under refs/gitrescue/backups/<id>. "
    "(Backups are also taken automatically before every change.)",
    input_model=CreateBackupInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_backup,
)
