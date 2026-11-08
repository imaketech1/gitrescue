from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gitrescue import __version__
from gitrescue.config import Settings
from gitrescue.git import GitError, repo_root

app = typer.Typer(
    name="gitrescue",
    help='Fix git mistakes from plain-language requests, safely.\n\nUsage: gitrescue "I committed to main instead of my feature branch"',
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _repo(path: Path) -> Path:
    try:
        return repo_root(path)
    except GitError:
        console.print(f"[red]Not a git repository:[/red] {path}")
        raise typer.Exit(2) from None


def _short(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _print_event(kind: str, rec: dict) -> None:
    if kind == "llm" and rec.get("content") and rec.get("tool_calls"):
        console.print(f"[dim]  thinking: {_short(rec['content'], 200)}[/dim]")
    elif kind == "tool":
        mark = "[green]✓[/green]" if rec["ok"] else ("[red]⛔[/red]" if rec.get("blocked") else "[yellow]✗[/yellow]")
        tier = rec.get("tier") or "?"
        console.print(f"{mark} [bold]{rec['tool']}[/bold] [dim]{tier}[/dim] {_short(rec.get('args'), 120)}")
        if rec.get("error"):
            console.print(f"    [dim]{_short(rec['error'], 300)}[/dim]")
        if rec.get("backup_id"):
            console.print(f"    [dim]backup: {rec['backup_id']}[/dim]")
    elif kind == "llm_error":
        console.print(f"[red]LLM error:[/red] {rec['error']}")


@app.command("fix")
def fix(
    request: Annotated[str, typer.Argument(help="What went wrong, in plain language.")],
    repo: Annotated[Path, typer.Option("--repo", "-C", help="Repository path.")] = Path("."),
    model: Annotated[str | None, typer.Option(help="Override GITRESCUE_MODEL.")] = None,
    max_steps: Annotated[int | None, typer.Option(help="Override the step cap.")] = None,
    sandbox: Annotated[bool, typer.Option("--sandbox/--no-sandbox", help="Work on a temporary copy and apply only after approval.")] = True,
    no_trace: Annotated[bool, typer.Option("--no-trace", help="Do not write a trace file.")] = False,
) -> None:
    """Diagnose and fix a git mistake described in plain language."""
    from gitrescue.agent.llm import LLMError, OpenAICompatibleClient
    from gitrescue.policy.approval import ConsoleApprover
    from gitrescue.runner import run_fix
    from gitrescue.sandbox import SandboxError
    from gitrescue.tracing import Tracer

    settings = Settings.from_env()
    if model:
        settings.model = model
    if max_steps:
        settings.max_steps = max_steps
    root = _repo(repo)
    try:
        llm = OpenAICompatibleClient(settings.model, settings.base_url, settings.api_key, settings.temperature)
    except LLMError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None

    tracer = Tracer(None if no_trace else settings.trace_dir)

    def ask(question: str) -> str | None:
        if not sys.stdin.isatty():
            return None
        return typer.prompt(f"\n❓ {question}\n>")

    def confirm_apply(plan) -> bool:
        console.print(render_plan(plan, root))
        if not sys.stdin.isatty():
            console.print("[yellow]stdin is not a terminal: not applied[/yellow]")
            return False
        return console.input("Apply these changes to your repository? [y/N] ").strip().lower() in {"y", "yes"}

    mode = "sandbox" if sandbox else "in place"
    console.print(Panel(f"[bold]{request}[/bold]\n[dim]{root} · {mode} · model {settings.model} · run {tracer.run_id}[/dim]", title="GitRescue"))
    try:
        out = run_fix(
            request, root, llm, settings, tracer, ConsoleApprover(console), confirm_apply,
            sandbox=sandbox, ask_user=ask, on_event=_print_event,
        )
    except SandboxError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    _report(out.run, tracer)
    if out.sandboxed:
        if out.plan is not None and out.plan.blocking_issue:
            console.print(f"[yellow]Not applied: {out.plan.blocking_issue}[/yellow]")
        elif out.plan is not None and out.plan.empty:
            console.print("[dim]No changes to apply.[/dim]")
        elif out.applied and out.apply_result:
            console.print(f"[green]Applied to {root}.[/green] Undo with backup [bold]{out.apply_result['backup_id']}[/bold] "
                          "(backup ids mentioned above refer to the sandbox copy).")
            for p in out.apply_result["pushes"]:
                status = "[green]pushed[/green]" if p["ok"] else f"[red]not pushed[/red]: {p['error']}"
                console.print(f"  push {p['args']['branch']}: {status}")
        elif out.apply_error:
            console.print(f"[red]Not applied: {out.apply_error}[/red]")
        else:
            console.print("[yellow]Not applied. Your repository is unchanged.[/yellow]")


def render_plan(plan, root: Path) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_row("[bold]HEAD[/bold]", f"{plan.head_before} → {plan.head_after}")
    for b in plan.branches:
        t.add_row(f"[bold]{b.kind}[/bold]", f"{b.name}: {(b.old or '-')[:10]} → {(b.new or '-')[:10]}")
        for c in b.commits_added[:10]:
            t.add_row("", f"[green]+ {c}[/green]")
        for c in b.commits_removed[:10]:
            t.add_row("", f"[red]- {c}[/red] [dim](kept in backup)[/dim]")
    if plan.files:
        t.add_row("[bold]files[/bold]", "\n".join(plan.files[:25]) + (f"\n… {len(plan.files) - 25} more" if len(plan.files) > 25 else ""))
    if plan.stash_before != plan.stash_after:
        t.add_row("[bold]stash[/bold]", f"{plan.stash_before} → {plan.stash_after} entries")
    for p in plan.pushes:
        t.add_row("[red]push[/red]", f"force-with-lease {p['branch']} → {p.get('remote', 'origin')} (asks again before pushing)")
    return Panel(t, title=f"Plan for {root}", border_style="cyan")


def _report(result, tracer) -> None:
    style = "green" if result.status == "completed" else "yellow"
    console.print(Panel(result.final_message or "(no message)", title=f"[{style}]{result.status}[/{style}]"))
    console.print(
        f"[dim]steps {result.steps} · tokens {result.total_tokens} · {result.latency_s}s"
        + (f" · trace {tracer.path}" if tracer.path else "")
        + "[/dim]"
    )


@app.command("replay")
def replay(
    run_id: Annotated[str | None, typer.Argument(help="Run id (prefix ok). Omit to list recent runs.")] = None,
    trace_dir: Annotated[Path | None, typer.Option(help="Trace directory.")] = None,
) -> None:
    """Print a recorded run step by step."""
    from gitrescue.tracing import list_runs, load_trace

    d = trace_dir or Settings.from_env().trace_dir
    if run_id is None:
        runs = list_runs(d)
        if not runs:
            console.print(f"No traces in {d}")
            return
        for p in runs[:20]:
            first = json.loads(p.read_text().splitlines()[0])
            console.print(f"{p.stem}  [dim]{_short(first.get('request', ''), 80)}[/dim]")
        return
    try:
        events = load_trace(d, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    table = Table(show_lines=False, expand=True)
    for col in ("step", "event", "tool / detail", "result", "tokens", "ms", "approval"):
        table.add_column(col, overflow="fold")
    for ev in events:
        kind = ev["event"]
        if kind == "run_start":
            console.print(
                Panel(
                    f"[bold]{ev['request']}[/bold]\nrepo {ev['repo']} · model {ev['model']} · prompt v{ev['prompt_version']} ({ev['prompt_sha']})",
                    title=f"run {ev['run_id']}",
                )
            )
        elif kind == "llm":
            calls = ", ".join(c["name"] for c in ev.get("tool_calls", [])) or "final answer"
            table.add_row(
                str(ev["step"]), "llm", calls, _short(ev.get("content") or "", 120),
                str(ev["prompt_tokens"] + ev["completion_tokens"]), str(ev["latency_ms"]), "",
            )
        elif kind == "tool":
            res = "ok" if ev["ok"] else ("BLOCKED: " if ev.get("blocked") else "ERR: ") + _short(ev.get("error") or "", 100)
            table.add_row(
                str(ev["step"]), "tool", f"{ev['tool']} {_short(ev.get('args'), 100)}", res, "",
                str(ev["latency_ms"]), ev.get("approval") or "",
            )
        elif kind == "ask_user":
            table.add_row(str(ev["step"]), "ask_user", ev["question"], ev.get("answer") or "(no answer)", "", "", "")
        elif kind == "run_end":
            console.print(table)
            console.print(
                Panel(
                    ev.get("final_message") or "",
                    title=f"{ev['status']} · steps {ev['steps']} · tokens {ev['prompt_tokens'] + ev['completion_tokens']} · {ev['latency_s']}s",
                )
            )
        else:
            table.add_row(str(ev.get("step", "")), kind, _short({k: v for k, v in ev.items() if k not in {"run_id", "ts", "event"}}, 160), "", "", "", "")


@app.command("eval")
def eval_cmd(
    reference: Annotated[bool, typer.Option("--reference", help="Run scripted oracle solutions (validates scenarios/harness; not a model eval).")] = False,
    only: Annotated[list[str] | None, typer.Option("--scenario", "-s", help="Run only these scenarios (repeatable).")] = None,
    kind: Annotated[str | None, typer.Option(help="standard | adversarial")] = None,
    repeat: Annotated[int, typer.Option(min=1, help="Run each scenario N times.")] = 1,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="JSON results path.")] = None,
    model: Annotated[str | None, typer.Option(help="Override GITRESCUE_MODEL.")] = None,
    traces: Annotated[bool, typer.Option("--traces/--no-traces", help="Write a trace per scenario run.")] = True,
) -> None:
    """Run the eval scenarios and report success rate, data loss, guardrail and cost metrics."""
    from datetime import UTC, datetime

    from gitrescue.agent.llm import LLMError, OpenAICompatibleClient
    from gitrescue.evals.harness import run_suite, save_report
    from gitrescue.evals.scenarios import ALL, BY_NAME

    settings = Settings.from_env()
    if model:
        settings.model = model
    scenarios = ALL
    if only:
        unknown = [n for n in only if n not in BY_NAME]
        if unknown:
            console.print(f"[red]Unknown scenario(s): {unknown}[/red]. Known: {sorted(BY_NAME)}")
            raise typer.Exit(2)
        scenarios = [BY_NAME[n] for n in only]
    if kind:
        scenarios = [s for s in scenarios if s.kind == kind]
    factory = None
    if not reference:
        try:
            OpenAICompatibleClient(settings.model, settings.base_url, settings.api_key)
        except LLMError as e:
            console.print(f"[red]{e}[/red] (or use --reference to validate the harness without a model)")
            raise typer.Exit(2) from None

        def factory(scenario, ctx):
            return OpenAICompatibleClient(settings.model, settings.base_url, settings.api_key, settings.temperature)

    mode = "reference" if reference else "model"
    console.print(f"[bold]GitRescue eval[/bold] · {mode} · {len(scenarios)} scenario(s) × {repeat}" + ("" if reference else f" · {settings.model}"))

    def on_result(r) -> None:
        mark = "[green]PASS[/green]" if r.success else "[red]FAIL[/red]"
        extra = []
        if r.check_failures:
            extra.append("failed: " + "; ".join(r.check_failures))
        if r.data_loss_incident:
            extra.append(f"[red]DATA LOSS[/red] {r.data_loss}")
        if r.guardrail_violations:
            extra.append(f"[red]VIOLATION[/red] {r.guardrail_violations}")
        if r.unnecessary_destructive:
            extra.append(f"unnecessary destructive: {r.unnecessary_destructive}")
        if r.error:
            extra.append(r.error)
        console.print(f"{mark} {r.name:<40} {r.status:<12} steps={r.steps:<3} {r.latency_s:>6.2f}s  " + " | ".join(extra))

    trace_dir = settings.trace_dir / "evals" if traces else None
    report = run_suite(scenarios, mode, settings, factory, trace_dir=trace_dir, repeat=repeat, on_result=on_result)
    s = report["summary"]
    table = Table(title=f"Summary ({mode})")
    table.add_column("metric")
    table.add_column("value", justify="right")
    rows = [
        ("success rate", f"{s['passed']}/{s['scenarios']} ({s['success_rate']:.0%})"),
        *[(f"  {k}", f"{v['passed']}/{v['scenarios']}") for k, v in s["by_kind"].items()],
        ("data-loss incidents", str(s["data_loss_incidents"])),
        ("guardrail violations", str(s["guardrail_violations"])),
        ("guardrail blocks (attempts refused)", str(s["guardrail_blocks"])),
        ("unnecessary destructive actions", str(s["unnecessary_destructive_actions"])),
        ("avg steps", str(s["avg_steps"])),
        ("avg tokens", "n/a (scripted)" if s["avg_tokens"] is None else str(s["avg_tokens"])),
        ("avg latency (s)", str(s["avg_latency_s"])),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = save_report(report, output or Path("evals-results") / f"{mode}-{stamp}.json")
    console.print(f"[dim]results: {path}[/dim]")
    if mode == "reference":
        console.print("[dim]Reference mode replays scripted oracle solutions: it checks scenarios and harness, not a model.[/dim]")


@app.command("mcp")
def mcp_cmd(
    repo: Annotated[Path, typer.Option("--repo", "-C", help="Repository the server operates on.")] = Path("."),
    trust_client_approval: Annotated[
        bool,
        typer.Option(help="Skip elicitation for DESTRUCTIVE tools and rely on the MCP host's own per-call confirmation."),
    ] = False,
    no_trace: Annotated[bool, typer.Option("--no-trace")] = False,
) -> None:
    """Run the MCP server (stdio) exposing the same tools and policy."""
    from gitrescue.mcp_server import main as mcp_main

    mcp_main(_repo(repo), trust_client_approval, None if no_trace else Settings.from_env().trace_dir)


@app.command("tools")
def list_tools() -> None:
    """List the tools available to the agent and their risk tiers."""
    from gitrescue.tools import ALL_TOOLS

    for t in ALL_TOOLS:
        console.print(f"[bold]{t.name}[/bold] [dim]{t.tier.value}[/dim] — {t.description}")


@app.command("inspect")
def inspect(
    tool: Annotated[str, typer.Argument(help="READ tool name, e.g. git_status")],
    args: Annotated[str, typer.Option("--args", help="JSON arguments")] = "{}",
    repo: Annotated[Path, typer.Option("--repo", "-C")] = Path("."),
) -> None:
    """Run a single READ tool directly (debugging aid)."""
    from gitrescue.tools import REGISTRY, RiskTier

    t = REGISTRY.get(tool)
    if t is None or t.tier is not RiskTier.READ:
        console.print(f"[red]Unknown READ tool:[/red] {tool}")
        raise typer.Exit(2)
    parsed = t.input_model.model_validate(json.loads(args))
    console.print_json(t.run(_repo(repo), parsed).model_dump_json())


@app.command("version")
def version() -> None:
    """Print the version."""
    console.print(__version__)


def main(argv: list[str] | None = None) -> None:
    """Entry point. ``gitrescue "<request>"`` is shorthand for ``gitrescue fix "<request>"``."""
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {c.name or (c.callback.__name__ if c.callback else "") for c in app.registered_commands}
    help_only = all(a in {"--help", "-h"} for a in args)
    if args and not help_only and not any(a in commands for a in args):
        args.insert(0, "fix")
    _run_app(args)


def _run_app(args: list[str]) -> None:
    app(args=args, prog_name="gitrescue")
