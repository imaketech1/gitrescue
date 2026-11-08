"""Policy enforcement, independent of the model.

Every tool call goes through :meth:`PolicyEngine.execute`, in this order:

1. allowlist (only registered tools of allowed tiers; there is no shell tool)
2. argument validation (Pydantic, extra fields forbidden)
3. hard blocks (protected branches, foreign branches, paths outside the repo, ...)
4. automatic backup before any non-READ tool
5. human approval with a preview for DESTRUCTIVE tools (injectable callback)
6. execution (the git layer underneath has its own guard as a second line)
7. invariant check after mutating tools (see ``invariants``), with auto-restore

Nothing the model says, and nothing in repository content, can change this
object's configuration: it is fixed when the engine is constructed.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gitrescue.git import PROTECTED_BRANCHES, git_ok, resolve_repo_path, run_git
from gitrescue.policy import backup as backups
from gitrescue.policy import invariants
from gitrescue.tools import REGISTRY, Preview, RiskTier, Tool, ToolInput, ToolResult

ALL_TIERS = frozenset(RiskTier)
NO_AUTO_BACKUP = {"create_backup_ref"}  # it *is* a backup


@dataclass
class ApprovalRequest:
    tool: str
    tier: RiskTier
    args: dict[str, Any]
    preview: Preview
    backup_id: str | None


ApprovalCallback = Callable[[ApprovalRequest], bool]


def deny_all(req: ApprovalRequest) -> bool:
    return False


def approve_all(req: ApprovalRequest) -> bool:
    return True


@dataclass
class Execution:
    tool: str
    args: dict[str, Any] | None
    result: ToolResult
    tier: RiskTier | None = None
    approval: str | None = None  # "approved" | "denied" | "not_required"
    backup_id: str | None = None
    latency_ms: float = 0.0
    events: list[str] = field(default_factory=list)
    preview: dict | None = None


class PolicyViolation(Exception):
    pass


# ---------------------------------------------------------------------------
# Hard blocks
# ---------------------------------------------------------------------------


def protected_branches(repo: Path, extra: frozenset[str] | set[str] = frozenset()) -> set[str]:
    names = set(PROTECTED_BRANCHES) | set(extra)
    env = os.environ.get("GITRESCUE_PROTECTED_BRANCHES", "")
    names |= {b.strip() for b in env.split(",") if b.strip()}
    # Each remote's default branch (refs/remotes/<r>/HEAD) is protected too.
    out = run_git(repo, "for-each-ref", "--format=%(refname)\x1f%(symref)", "refs/remotes")
    for line in out.stdout.splitlines():
        ref, _, sym = line.partition("\x1f")
        if ref.endswith("/HEAD") and sym:
            names.add(sym.split("/", 3)[-1])
    return names


def _remote_default_tips(repo: Path, remote: str) -> list[str]:
    tips = []
    for name in sorted(protected_branches(repo)):
        res = run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{name}")
        if res.ok:
            tips.append(res.stdout.strip())
    return tips


def check_push(repo: Path, branch: str, remote: str, protected: set[str]) -> str | None:
    if any(c in branch for c in ":+*") or branch.startswith(("refs/", "-")) or branch == "HEAD":
        return f"push target {branch!r} must be a plain branch name"
    if branch in protected:
        return f"force-pushing to protected branch {branch!r} is never allowed"
    email = run_git(repo, "config", "--get", "user.email").stdout.strip().lower()
    if not email:
        return "cannot verify branch ownership: user.email is not configured"
    tip = run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}")
    if not tip.ok:
        return None  # new remote branch: nothing of anyone else's can be overwritten
    excludes = [f"^{t}" for t in _remote_default_tips(repo, remote)]
    authors = git_ok(repo, "log", "--format=%ae", "--end-of-options", tip.stdout.strip(), *excludes).split()
    foreign = sorted({a for a in authors if a.lower() != email})
    if foreign:
        return f"{remote}/{branch} contains commits by other authors ({', '.join(foreign[:5])}); it is not your branch"
    return None


def hard_block(repo: Path, tool: str, args: dict[str, Any], protected: set[str]) -> str | None:
    """Return a reason if this call must be refused regardless of approval."""
    for key, value in args.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and "\x00" in v:
                return f"argument {key!r} contains a NUL byte"
    for key in ("file", "path"):
        if isinstance(args.get(key), str):
            try:
                resolve_repo_path(repo, args[key])
            except ValueError as e:
                return str(e)
    for p in args.get("paths") or []:
        try:
            resolve_repo_path(repo, p)
        except ValueError as e:
            return str(e)
    if tool == "push_force_with_lease":
        return check_push(repo, str(args.get("branch", "")), str(args.get("remote", "origin")), protected)
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    def __init__(
        self,
        repo: Path,
        approval: ApprovalCallback | None = None,
        allowed_tiers: frozenset[RiskTier] = ALL_TIERS,
        run_id: str | None = None,
        protected_extra: frozenset[str] = frozenset(),
        check_invariants: bool = True,
        deferred_pushes: list[dict[str, Any]] | None = None,
    ):
        self.repo = Path(repo).resolve()
        self.approval = approval or deny_all  # safe default: nothing destructive without a human
        self.allowed_tiers = frozenset(allowed_tiers)
        self.run_id = run_id or backups.new_backup_id("run")
        self.protected_extra = frozenset(protected_extra)
        self.check_invariants = check_invariants
        self.tools: dict[str, Tool] = {n: t for n, t in REGISTRY.items() if t.tier in self.allowed_tiers}
        self.backup_ids: list[str] = []
        # Sandbox mode: pushes are recorded here and replayed on the real repo after approval.
        self.deferred_pushes = deferred_pushes
        self._n = 0

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def parse(self, name: str, raw_args: str | dict | None) -> tuple[Tool | None, ToolInput | None, ToolResult | None]:
        tool = self.tools.get(name)
        if tool is None:
            return None, None, ToolResult.refuse(f"blocked: tool {name!r} is not on the allowlist")
        try:
            data = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            if not isinstance(data, dict):
                raise ValueError("arguments must be a JSON object")
            args = tool.input_model.model_validate(data)
        except (ValueError, ValidationError) as e:
            return tool, None, ToolResult.fail(f"invalid arguments for {name}: {e}")
        return tool, args, None

    def preflight(self, name: str, raw_args: str | dict | None) -> tuple[ToolResult | None, Preview | None]:
        """Validation + hard blocks + preview, with no side effects.

        Lets a front end (e.g. the MCP server) obtain human approval asynchronously
        before calling :meth:`execute`, which re-runs every check itself.
        """
        tool, args, err = self.parse(name, raw_args)
        if err is not None:
            return err, None
        assert tool is not None and args is not None
        reason = hard_block(self.repo, name, args.model_dump(), protected_branches(self.repo, self.protected_extra))
        if reason:
            return ToolResult.refuse(f"blocked by policy: {reason}"), None
        if tool.tier is not RiskTier.DESTRUCTIVE:
            return None, None
        try:
            return None, tool.preview(self.repo, args) if tool.preview else Preview(summary=f"{name} {args.model_dump()}")
        except Exception as e:
            return ToolResult.fail(f"cannot preview {name}: {e}"), None

    def _next_backup_id(self) -> str:
        while True:
            self._n += 1
            bid = f"{self.run_id}-{self._n:02d}"
            if not backups.backup_exists(self.repo, bid):
                return bid

    def execute(self, name: str, raw_args: str | dict | None) -> Execution:
        t0 = time.perf_counter()
        tool, args, err = self.parse(name, raw_args)
        if err is not None:
            return Execution(name, None, err, tool.tier if tool else None, latency_ms=_ms(t0))
        assert tool is not None and args is not None
        argd = args.model_dump()
        ex = Execution(name, argd, ToolResult.fail("not executed"), tool.tier, "not_required")

        reason = hard_block(self.repo, name, argd, protected_branches(self.repo, self.protected_extra))
        if reason:
            ex.result = ToolResult.refuse(f"blocked by policy: {reason}")
            ex.events.append("hard_block")
            ex.latency_ms = _ms(t0)
            return ex

        if name == "push_force_with_lease" and self.deferred_pushes is not None:
            self.deferred_pushes.append(argd)
            ex.result = ToolResult.success(
                {"deferred": True, "note": "sandbox mode: this push will run against the real repository only after "
                 "the user approves the final plan (and approves the push itself)."}
            )
            ex.events.append("deferred")
            ex.latency_ms = _ms(t0)
            return ex

        before_state = None
        before_reachable: set[str] = set()
        if tool.tier is not RiskTier.READ and self.check_invariants:
            before_reachable = invariants.reachable_commits(self.repo)
        if tool.tier is not RiskTier.READ and name not in NO_AUTO_BACKUP:
            try:
                b = backups.create_backup(self.repo, f"before {name}", self._next_backup_id())
            except Exception as e:  # never mutate without a backup
                ex.result = ToolResult.refuse(f"blocked: could not create backup ({e})")
                ex.events.append("backup_failed")
                ex.latency_ms = _ms(t0)
                return ex
            ex.backup_id = b.id
            before_state = b.state
            self.backup_ids.append(b.id)
            ex.events.append("backup")

        if tool.tier is RiskTier.DESTRUCTIVE:
            try:
                preview = tool.preview(self.repo, args) if tool.preview else Preview(summary=f"{name} {argd}")
            except Exception as e:
                ex.result = ToolResult.fail(f"cannot preview {name}: {e}", None)
                ex.result.backup_id = ex.backup_id
                ex.latency_ms = _ms(t0)
                return ex
            ex.preview = {
                "summary": preview.summary,
                "commits_moved": preview.commits_moved,
                "files_changed": preview.files_changed,
                "warnings": preview.warnings,
            }
            try:
                approved = bool(self.approval(ApprovalRequest(name, tool.tier, argd, preview, ex.backup_id)))
            except Exception:
                approved = False
            ex.approval = "approved" if approved else "denied"
            if not approved:
                ex.result = ToolResult.refuse(
                    f"denied: the user did not approve {name}. Do not retry the same call; explain or choose another approach."
                )
                ex.result.backup_id = ex.backup_id
                ex.latency_ms = _ms(t0)
                return ex

        ex.result = tool.run(self.repo, args)
        ex.result.backup_id = ex.backup_id
        if before_state is not None and self.check_invariants:
            self._after_mutation(ex, before_state, before_reachable)
        ex.latency_ms = _ms(t0)
        return ex

    def _after_mutation(self, ex: Execution, before_state: backups.RepoState, before_reachable: set[str]) -> None:
        """Invariant: nothing reachable before the step may be lost unless a backup holds it.

        On violation, restore the pre-step state and report an error to the agent.
        """
        try:
            report = invariants.check(self.repo, before_reachable, before_state)
        except Exception as e:  # if we cannot verify, treat it as a violation
            report = invariants.InvariantReport(ok=False)
            report_desc = f"could not verify invariant ({e})"
        else:
            report_desc = report.describe()
        if report.ok:
            return
        ex.events.append("invariant_violation")
        data = {"lost_commits": report.lost_commits, "lost_worktree": report.lost_worktree, "tool_result": ex.result.data}
        try:
            # Pin the pre-step objects first (they are still in the object store), then restore.
            pinned = backups.create_backup(self.repo, f"recovery after {ex.tool}", self._next_backup_id() + "-recovery", before_state)
            backups.apply_state(self.repo, before_state)
            ex.events.append("auto_restored")
            data["restored"] = True
            data["recovery_backup"] = pinned.id
            msg = f"invariant_violation: {report_desc}. The repository was restored to its state before {ex.tool}."
        except Exception as e:
            data["restored"] = False
            msg = f"invariant_violation: {report_desc}. Automatic restore FAILED ({e}); restore backup {ex.backup_id} manually."
        ex.result = ToolResult(ok=False, error=msg, data=data, blocked=True, backup_id=ex.backup_id)

def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
