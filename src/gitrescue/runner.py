"""One full GitRescue run: agent (optionally in a sandbox) + apply step."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gitrescue.agent.llm import LLMClient
from gitrescue.agent.loop import Agent, RunResult
from gitrescue.config import Settings
from gitrescue.policy.guardrails import ApprovalCallback, PolicyEngine
from gitrescue.sandbox import Sandbox, SandboxError, SandboxPlan
from gitrescue.tracing import Tracer


@dataclass
class FixOutcome:
    run: RunResult
    sandboxed: bool
    plan: SandboxPlan | None = None
    applied: bool = False
    apply_result: dict[str, Any] | None = None
    apply_error: str | None = None


def run_fix(
    request: str,
    repo: Path,
    llm: LLMClient,
    settings: Settings,
    tracer: Tracer,
    approval: ApprovalCallback,
    confirm_apply: Callable[[SandboxPlan], bool],
    sandbox: bool = True,
    ask_user: Callable[[str], str | None] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> FixOutcome:
    if not sandbox:
        engine = PolicyEngine(repo, approval=approval, run_id=tracer.run_id)
        result = Agent(llm, engine, settings, tracer, ask_user=ask_user, on_event=on_event).run(request)
        return FixOutcome(result, sandboxed=False)

    sb = Sandbox(repo)
    try:
        tracer.emit("sandbox", path=str(sb.path), real=str(sb.real))
        engine = PolicyEngine(sb.path, approval=approval, run_id=tracer.run_id, deferred_pushes=sb.deferred_pushes)
        result = Agent(llm, engine, settings, tracer, ask_user=ask_user, on_event=on_event).run(request)
        plan = sb.plan()
        out = FixOutcome(result, sandboxed=True, plan=plan)
        tracer.emit("sandbox_plan", **plan_to_dict(plan))
        if plan.empty or plan.blocking_issue:
            return out
        approved = bool(confirm_apply(plan))
        tracer.emit("apply_decision", approved=approved)
        if not approved:
            return out
        real_engine = PolicyEngine(repo, approval=approval, run_id=f"{tracer.run_id}-apply")
        try:
            out.apply_result = sb.apply(tracer.run_id, push_executor=lambda a: real_engine.execute("push_force_with_lease", a))
            out.applied = True
        except SandboxError as e:
            out.apply_error = str(e)
        tracer.emit("apply", applied=out.applied, result=out.apply_result, error=out.apply_error)
        return out
    finally:
        sb.cleanup()


def plan_to_dict(plan: SandboxPlan) -> dict[str, Any]:
    return {
        "branches": [b.__dict__ for b in plan.branches],
        "head_before": plan.head_before,
        "head_after": plan.head_after,
        "files": plan.files[:200],
        "stash_before": plan.stash_before,
        "stash_after": plan.stash_after,
        "pushes": plan.pushes,
        "blocking_issue": plan.blocking_issue,
    }
