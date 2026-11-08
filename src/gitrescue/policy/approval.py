"""Interactive approval prompt with a Rich preview."""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gitrescue.policy.guardrails import ApprovalRequest


def render_preview(req: ApprovalRequest) -> Panel:
    p = req.preview
    t = Table.grid(padding=(0, 1))
    t.add_row("[bold]tool[/bold]", f"{req.tool} [red]{req.tier.value}[/red]")
    t.add_row("[bold]args[/bold]", json.dumps({k: v for k, v in req.args.items() if k != "content"}, default=str)[:300])
    t.add_row("[bold]action[/bold]", p.summary)
    if p.commits_moved:
        t.add_row("[bold]commits[/bold]", "\n".join(p.commits_moved[:20]) + (f"\n… {len(p.commits_moved) - 20} more" if len(p.commits_moved) > 20 else ""))
    if p.files_changed:
        t.add_row("[bold]files[/bold]", "\n".join(p.files_changed[:20]) + (f"\n… {len(p.files_changed) - 20} more" if len(p.files_changed) > 20 else ""))
    for w in p.warnings:
        t.add_row("[yellow]warning[/yellow]", w)
    if req.backup_id:
        t.add_row("[bold]backup[/bold]", f"{req.backup_id}  (restore with restore_backup)")
    return Panel(t, title="[red]Approval required[/red]", border_style="red")


class ConsoleApprover:
    """Ask the human on the terminal. Non-interactive sessions are always denied."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def __call__(self, req: ApprovalRequest) -> bool:
        self.console.print(render_preview(req))
        if not sys.stdin.isatty():
            self.console.print("[yellow]stdin is not a terminal: denied[/yellow]")
            return False
        answer = self.console.input("Approve? [y/N] ").strip().lower()
        return answer in {"y", "yes"}
