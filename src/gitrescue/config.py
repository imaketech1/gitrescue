from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def _float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _default_trace_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "gitrescue" / "traces"


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    max_steps: int = 15
    max_tokens: int = 200_000
    max_cost_usd: float | None = None
    cost_per_mtok_in: float | None = None
    cost_per_mtok_out: float | None = None
    temperature: float = 0.0
    trace_dir: Path = field(default_factory=_default_trace_dir)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            model=os.environ.get("GITRESCUE_MODEL") or DEFAULT_MODEL,
            base_url=os.environ.get("GITRESCUE_BASE_URL") or DEFAULT_BASE_URL,
            api_key=os.environ.get("GITRESCUE_API_KEY") or None,
            max_steps=int(os.environ.get("GITRESCUE_MAX_STEPS") or 15),
            max_tokens=int(os.environ.get("GITRESCUE_MAX_TOKENS") or 200_000),
            max_cost_usd=_float("GITRESCUE_MAX_COST_USD", None),
            cost_per_mtok_in=_float("GITRESCUE_COST_PER_MTOK_IN", None),
            cost_per_mtok_out=_float("GITRESCUE_COST_PER_MTOK_OUT", None),
            temperature=_float("GITRESCUE_TEMPERATURE", 0.0) or 0.0,
            trace_dir=Path(os.environ["GITRESCUE_TRACE_DIR"]) if os.environ.get("GITRESCUE_TRACE_DIR") else _default_trace_dir(),
        )

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        if self.cost_per_mtok_in is None or self.cost_per_mtok_out is None:
            return None
        return prompt_tokens / 1e6 * self.cost_per_mtok_in + completion_tokens / 1e6 * self.cost_per_mtok_out
