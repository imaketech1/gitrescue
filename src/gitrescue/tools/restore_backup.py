from __future__ import annotations

from pathlib import Path

from pydantic import Field

from gitrescue.policy.backup import restore
from gitrescue.tools.base import RiskTier, Tool, ToolInput, ToolResult


class RestoreBackupInput(ToolInput):
    backup_id: str = Field(description="Id returned when the backup was created.")


def _restore(repo: Path, a: RestoreBackupInput) -> ToolResult:
    return ToolResult.success(restore(repo, a.backup_id))


TOOL = Tool(
    name="restore_backup",
    description="Restore branches, HEAD, stash, index and working tree to a backup. The current state is "
    "backed up first, so this is itself reversible.",
    input_model=RestoreBackupInput,
    tier=RiskTier.LOCAL_REVERSIBLE,
    func=_restore,
)
