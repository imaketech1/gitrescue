"""Tool registry. Each tool lives in its own module and exposes ``TOOL``."""

from __future__ import annotations

from gitrescue.tools import (
    checkout,
    cherry_pick,
    commit,
    create_backup_ref,
    create_branch,
    git_diff,
    git_log,
    git_reflog,
    git_status,
    list_backups,
    list_branches,
    push_force_with_lease,
    rebase,
    rename_branch,
    reset,
    resolve_conflict,
    restore_backup,
    show_commit,
    stage,
    stash,
    unstage,
)
from gitrescue.tools.base import Preview, RiskTier, Tool, ToolInput, ToolResult

READ_TOOLS: list[Tool] = [
    git_status.TOOL,
    git_log.TOOL,
    git_reflog.TOOL,
    git_diff.TOOL,
    list_branches.TOOL,
    show_commit.TOOL,
    list_backups.TOOL,
]

LOCAL_REVERSIBLE_TOOLS: list[Tool] = [
    create_branch.TOOL,
    checkout.TOOL,
    cherry_pick.TOOL,
    stash.TOOL,
    create_backup_ref.TOOL,
    restore_backup.TOOL,
    stage.TOOL,
    unstage.TOOL,
    commit.TOOL,
    rename_branch.TOOL,
]

DESTRUCTIVE_TOOLS: list[Tool] = [
    reset.TOOL,
    rebase.TOOL,
    resolve_conflict.TOOL,
    push_force_with_lease.TOOL,
]

ALL_TOOLS: list[Tool] = [*READ_TOOLS, *LOCAL_REVERSIBLE_TOOLS, *DESTRUCTIVE_TOOLS]

REGISTRY: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}

__all__ = ["ALL_TOOLS", "DESTRUCTIVE_TOOLS", "LOCAL_REVERSIBLE_TOOLS", "READ_TOOLS", "REGISTRY", "Preview", "RiskTier", "Tool", "ToolInput", "ToolResult"]
