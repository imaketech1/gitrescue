from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from gitrescue.git import GitBlocked, GitError


class RiskTier(str, Enum):
    READ = "READ"
    LOCAL_REVERSIBLE = "LOCAL_REVERSIBLE"
    DESTRUCTIVE = "DESTRUCTIVE"


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str | None = None
    blocked: bool = False  # True when refused by policy/guardrails (not a git failure)
    backup_id: str | None = None  # backup taken before this call, if any

    @classmethod
    def success(cls, data: Any = None) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def fail(cls, error: str, data: Any = None) -> ToolResult:
        return cls(ok=False, error=error, data=data)

    @classmethod
    def refuse(cls, error: str) -> ToolResult:
        return cls(ok=False, error=error, blocked=True)


@dataclass
class Preview:
    """What a mutating tool is about to do, for approval prompts."""

    summary: str
    commits_moved: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


ToolFunc = Callable[[Path, Any], ToolResult]
PreviewFunc = Callable[[Path, Any], Preview]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    tier: RiskTier
    func: ToolFunc
    preview: PreviewFunc | None = None

    def schema(self) -> dict:
        params = self.input_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"[{self.tier.value}] {self.description}",
                "parameters": params,
            },
        }

    def run(self, repo: Path, args: ToolInput) -> ToolResult:
        """Run the tool function, converting any exception into a structured failure."""
        try:
            result = self.func(repo, args)
        except GitBlocked as e:
            return ToolResult.refuse(f"blocked: {e}")
        except GitError as e:
            return ToolResult.fail(f"git error: {e}")
        except ValueError as e:
            return ToolResult.fail(f"invalid input: {e}")
        except Exception as e:  # never leak raw exceptions to the agent
            return ToolResult.fail(f"{type(e).__name__}: {e}")
        if not isinstance(result, ToolResult):
            return ToolResult.fail("tool returned an invalid result")
        return result
