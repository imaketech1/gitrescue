"""LLM clients. The agent only depends on the small ``LLMClient`` protocol."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class LLMError(Exception):
    pass


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint (Groq by default)."""

    def __init__(self, model: str, base_url: str, api_key: str | None, temperature: float = 0.0, timeout: float = 120):
        if not api_key:
            raise LLMError("No API key: set GITRESCUE_API_KEY (and optionally GITRESCUE_BASE_URL / GITRESCUE_MODEL).")
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=2)

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
                parallel_tool_calls=False,
            )
        except Exception as e:  # surface provider errors as one type
            raise LLMError(f"{type(e).__name__}: {e}") from e
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        usage = resp.usage
        return LLMResponse(
            content=msg.content,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


Step = LLMResponse | Callable[[list[dict]], LLMResponse]


class ScriptedLLM:
    """Deterministic client for tests and oracle runs.

    Each step is either a fixed LLMResponse or a callable receiving the message
    history (so a script can react to earlier tool results). When the script
    runs out it returns a final answer.
    """

    model = "scripted"

    def __init__(self, steps: Sequence[Step], final: str = "Done."):
        self.steps = list(steps)
        self.final = final
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.calls.append(list(messages))
        if not self.steps:
            return LLMResponse(content=self.final, prompt_tokens=10, completion_tokens=5)
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


_counter = 0


def call(name: str, args: dict[str, Any] | str | None = None, content: str | None = None) -> LLMResponse:
    """Helper to build a single-tool-call response."""
    global _counter
    _counter += 1
    raw = args if isinstance(args, str) else json.dumps(args or {})
    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(id=f"call_{_counter}", name=name, arguments=raw)],
        prompt_tokens=100,
        completion_tokens=20,
    )


def final(text: str) -> LLMResponse:
    return LLMResponse(content=text, prompt_tokens=100, completion_tokens=30)
