"""Hand-written tool-calling loop.

Each step: send history + tool schemas -> parse tool calls -> validate args ->
check policy -> execute -> append result. Stops on a final answer, ask_user,
step cap, token/cost cap, or a repeated identical failing call.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from gitrescue.agent.llm import LLMClient, LLMError, ToolCall
from gitrescue.config import Settings
from gitrescue.policy.guardrails import Execution, PolicyEngine
from gitrescue.prompts import load_system_prompt
from gitrescue.tools import ToolResult
from gitrescue.tracing import NullTracer, Tracer

MAX_RESULT_CHARS = 16_000

ASK_USER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "[CONTROL] Ask the user a clarifying question when the request is ambiguous. "
        "Use before making any change if you are unsure what they want.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "One specific question."}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}

STOP_COMPLETED = "completed"
STOP_NEEDS_INPUT = "needs_input"
STOP_STEP_CAP = "step_cap"
STOP_TOKEN_CAP = "token_cap"
STOP_COST_CAP = "cost_cap"
STOP_LOOP = "loop_detected"
STOP_LLM_ERROR = "llm_error"


@dataclass
class RunResult:
    run_id: str
    status: str
    final_message: str | None = None
    question: str | None = None
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    executions: list[Execution] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def render_tool_result(tool: str, result: ToolResult) -> str:
    """Serialize a tool result as clearly delimited, untrusted data for the model.

    Angle brackets are escaped inside the JSON so repository content can never
    close the wrapper tag or open a fake one.
    """
    payload = json.dumps(result.model_dump(exclude_none=True), ensure_ascii=False, default=str)
    if len(payload) > MAX_RESULT_CHARS:
        payload = json.dumps(
            {
                "ok": result.ok,
                "error": result.error,
                "truncated_json": payload[:MAX_RESULT_CHARS],
                "note": "result too large and was truncated; narrow the request",
            },
            ensure_ascii=False,
        )
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    safe_tool = "".join(c for c in tool if c.isalnum() or c in "_-")[:64]
    return f'<tool_result tool="{safe_tool}" untrusted="true">\n{payload}\n</tool_result>'


def _signature(call: ToolCall) -> str:
    try:
        canon = json.dumps(json.loads(call.arguments or "{}"), sort_keys=True)
    except json.JSONDecodeError:
        canon = call.arguments
    return f"{call.name}:{canon}"


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        engine: PolicyEngine,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        ask_user: Callable[[str], str | None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self.llm = llm
        self.engine = engine
        self.settings = settings or Settings()
        self.tracer = tracer or NullTracer()
        self.ask_user = ask_user
        self.on_event = on_event or (lambda kind, data: None)

    def _emit(self, event: str, **fields) -> None:
        rec = self.tracer.emit(event, **fields)
        self.on_event(event, rec)

    def run(self, request: str) -> RunResult:
        prompt, version, digest = load_system_prompt()
        s = self.settings
        result = RunResult(run_id=self.tracer.run_id, status=STOP_STEP_CAP)
        t_start = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Repository: {self.engine.repo}\n\nRequest: {request}"},
        ]
        tools = [*self.engine.schemas(), ASK_USER_SCHEMA]
        self._emit(
            "run_start",
            request=request,
            repo=str(self.engine.repo),
            model=getattr(self.llm, "model", "?"),
            prompt_version=version,
            prompt_sha=digest,
            tools=[t["function"]["name"] for t in tools],
        )
        failures: dict[str, int] = {}
        stop: str | None = None

        for step in range(1, s.max_steps + 1):
            result.steps = step
            t0 = time.perf_counter()
            try:
                resp = self.llm.complete(messages, tools)
            except LLMError as e:
                result.final_message = str(e)
                stop = STOP_LLM_ERROR
                self._emit("llm_error", step=step, error=str(e))
                break
            llm_ms = round((time.perf_counter() - t0) * 1000, 2)
            result.prompt_tokens += resp.prompt_tokens
            result.completion_tokens += resp.completion_tokens
            result.cost_usd = s.cost(result.prompt_tokens, result.completion_tokens)
            self._emit(
                "llm",
                step=step,
                content=resp.content,
                tool_calls=[{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                latency_ms=llm_ms,
            )

            if not resp.tool_calls:
                result.final_message = resp.content or ""
                stop = STOP_COMPLETED
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                        for c in resp.tool_calls
                    ],
                }
            )

            for c in resp.tool_calls:
                if c.name == "ask_user":
                    stop = self._handle_ask(c, messages, result, step)
                    continue
                if stop is not None:
                    # A previous call in this same turn ended the run; answer remaining calls so history stays valid.
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": "skipped: run stopped"})
                    continue
                ex = self.engine.execute(c.name, c.arguments)
                result.executions.append(ex)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": render_tool_result(c.name, ex.result)})
                self._emit(
                    "tool",
                    step=step,
                    tool=c.name,
                    args=ex.args if ex.args is not None else c.arguments,
                    tier=ex.tier.value if ex.tier else None,
                    ok=ex.result.ok,
                    blocked=ex.result.blocked,
                    error=ex.result.error,
                    result=ex.result.data,
                    approval=ex.approval,
                    backup_id=ex.backup_id,
                    policy_events=ex.events,
                    latency_ms=ex.latency_ms,
                )
                if not ex.result.ok:
                    sig = _signature(c)
                    failures[sig] = failures.get(sig, 0) + 1
                    if failures[sig] >= 2:
                        stop = STOP_LOOP
                        result.final_message = f"Stopped: repeated identical failing call {c.name}: {ex.result.error}"
            if stop is not None:
                break

            if result.total_tokens > s.max_tokens:
                stop = STOP_TOKEN_CAP
                result.final_message = f"Stopped: token budget exceeded ({result.total_tokens} > {s.max_tokens})."
                break
            if s.max_cost_usd is not None and result.cost_usd is not None and result.cost_usd > s.max_cost_usd:
                stop = STOP_COST_CAP
                result.final_message = f"Stopped: cost budget exceeded (${result.cost_usd:.4f})."
                break

        result.status = stop or STOP_STEP_CAP
        if result.status == STOP_STEP_CAP:
            result.final_message = f"Stopped: reached the step cap ({s.max_steps})."
        result.latency_s = round(time.perf_counter() - t_start, 3)
        self._emit(
            "run_end",
            status=result.status,
            final_message=result.final_message,
            question=result.question,
            steps=result.steps,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_s=result.latency_s,
        )
        return result

    def _handle_ask(self, c: ToolCall, messages: list[dict], result: RunResult, step: int) -> str | None:
        try:
            question = str(json.loads(c.arguments or "{}").get("question", "")).strip()
        except (json.JSONDecodeError, AttributeError):
            question = ""
        question = question or "Could you clarify what you want me to do?"
        answer = self.ask_user(question) if self.ask_user else None
        self._emit("ask_user", step=step, question=question, answer=answer)
        if answer is None:
            messages.append({"role": "tool", "tool_call_id": c.id, "content": "No answer available; the run will stop."})
            result.question = question
            result.final_message = question
            return STOP_NEEDS_INPUT
        messages.append({"role": "tool", "tool_call_id": c.id, "content": f"User answered: {answer}"})
        return None
