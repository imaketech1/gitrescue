"""Scenario definition shared by all eval scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gitrescue.agent.llm import Step
from gitrescue.agent.loop import RunResult
from gitrescue.fixtures import RepoBuilder
from gitrescue.git import git_ok, run_git

Expect = Literal["fix", "refuse", "ask"]


@dataclass
class Ctx:
    """Everything a scenario's setup produced."""

    root: Path  # temp dir holding repo, remote, canary
    repo: Path
    rb: RepoBuilder
    remote: Path | None = None
    info: dict[str, Any] = field(default_factory=dict)

    def git(self, *args: str) -> str:
        return git_ok(self.repo, *args, _guard=False)

    def ok(self, *args: str) -> bool:
        return run_git(self.repo, *args, _guard=False).ok

    def sha(self, rev: str) -> str | None:
        res = run_git(self.repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", _guard=False)
        return res.stdout.strip() if res.ok else None

    def remote_sha(self, branch: str) -> str | None:
        if self.remote is None:
            return None
        res = run_git(self.remote, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", _guard=False)
        return res.stdout.strip() if res.ok else None

    def file(self, rel: str) -> str | None:
        p = self.repo / rel
        return p.read_text() if p.exists() else None

    def tree_file(self, rev: str, rel: str) -> str | None:
        res = run_git(self.repo, "show", f"{rev}:{rel}", _guard=False)
        return res.stdout if res.ok else None

    def subjects(self, rev: str, n: int = 10) -> list[str]:
        return self.git("log", f"--max-count={n}", "--format=%s", rev).splitlines()

    def staged(self) -> list[str]:
        return self.git("diff", "--cached", "--name-only").split()

    def in_progress(self) -> bool:
        gd = self.repo / ".git"
        return any((gd / f).exists() for f in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply"))


@dataclass
class Check:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, conditions: dict[str, bool]) -> Check:
        failed = [name for name, ok in conditions.items() if not ok]
        return cls(not failed, failed)


@dataclass
class Scenario:
    name: str
    kind: Literal["standard", "adversarial"]
    request: str
    setup: Callable[[Path], Ctx]
    check: Callable[[Ctx, RunResult], Check]
    expect: Expect = "fix"
    allowed_destructive: frozenset[str] = frozenset()
    reference: Callable[[Ctx], list[Step]] | None = None
    description: str = ""


def new_repo(root: Path, with_remote: bool = True) -> Ctx:
    rb = RepoBuilder(root / "repo")
    ctx = Ctx(root=root, repo=rb.path, rb=rb)
    if with_remote:
        ctx.remote = root / "remote.git"
    return ctx


def publish(ctx: Ctx, *branches: str) -> None:
    """Create the bare remote (first call) and push branches with upstream tracking."""
    assert ctx.remote is not None
    if not ctx.remote.exists():
        ctx.rb.add_bare_remote(ctx.remote)
    ctx.rb.push(*branches)
