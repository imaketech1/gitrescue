"""JSONL tracing: one file per run, one JSON object per event."""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


class Tracer:
    def __init__(self, trace_dir: Path | None, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.path: Path | None = None
        self.events: list[dict] = []
        if trace_dir is not None:
            trace_dir = Path(trace_dir)
            trace_dir.mkdir(parents=True, exist_ok=True)
            self.path = trace_dir / f"{self.run_id}.jsonl"

    def emit(self, event: str, **fields: Any) -> dict:
        rec = {"run_id": self.run_id, "ts": time.time(), "event": event, **fields}
        self.events.append(rec)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec


class NullTracer(Tracer):
    def __init__(self, run_id: str | None = None):
        super().__init__(None, run_id)


def load_trace(trace_dir: Path, run_id: str) -> list[dict]:
    path = Path(trace_dir) / f"{run_id}.jsonl"
    if not path.exists():
        matches = sorted(Path(trace_dir).glob(f"{run_id}*.jsonl"))
        if len(matches) != 1:
            raise FileNotFoundError(f"no unique trace for run id {run_id!r} in {trace_dir}")
        path = matches[0]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_runs(trace_dir: Path) -> list[Path]:
    d = Path(trace_dir)
    return sorted(d.glob("*.jsonl"), reverse=True) if d.exists() else []
