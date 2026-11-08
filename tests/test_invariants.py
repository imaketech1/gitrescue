from __future__ import annotations

import pytest

from gitrescue.git import git_ok
from gitrescue.policy import backup as backups
from gitrescue.policy import guardrails, invariants
from gitrescue.policy.guardrails import PolicyEngine, approve_all
from gitrescue.tools import RiskTier, Tool, ToolInput, ToolResult


@pytest.fixture
def broken_backups(monkeypatch):
    """Simulate a backup step that records the snapshot but pins no refs."""
    real = backups.create_backup

    def fake(repo, label, backup_id=None, state=None):
        if backup_id and backup_id.endswith("-recovery"):
            return real(repo, label, backup_id, state)
        state = state or backups.capture_state(repo)
        return backups.Backup(backup_id or "x", label, "now", state)

    monkeypatch.setattr(guardrails.backups, "create_backup", fake)


def engine(rb, **kw):
    return PolicyEngine(rb.path, approval=approve_all, run_id="inv", **kw)


def test_normal_destructive_step_passes(rb):
    lost = rb.sha()
    ex = engine(rb).execute("reset", {"mode": "hard", "target": "HEAD~1"})
    assert ex.result.ok and "invariant_violation" not in ex.events
    assert rb.git("cat-file", "-t", lost).strip() == "commit"


def test_lost_commit_without_backup_is_restored(rb, broken_backups):
    head = rb.sha()
    ex = engine(rb).execute("reset", {"mode": "hard", "target": "HEAD~2"})
    assert not ex.result.ok and ex.result.error.startswith("invariant_violation")
    assert ex.events[-2:] == ["invariant_violation", "auto_restored"]
    assert head in ex.result.data["lost_commits"]
    assert rb.sha() == head and rb.read("app.py") == "print('v2')\n"
    # and the commits are now pinned by the recovery backup
    rec = ex.result.data["recovery_backup"]
    assert rb.git("rev-parse", f"refs/gitrescue/backups/{rec}/heads/main").strip() == head


def test_discarded_worktree_without_backup_is_restored(rb, broken_backups):
    rb.write("app.py", "precious uncommitted work\n")
    ex = engine(rb).execute("reset", {"mode": "hard", "target": "HEAD"})
    assert not ex.result.ok and ex.result.data["lost_worktree"] is True
    assert rb.read("app.py") == "precious uncommitted work\n"


def test_clean_worktree_reset_without_backup_is_fine(rb, broken_backups):
    # Nothing uncommitted and nothing lost (target is an ancestor kept by `feature`? no: HEAD itself)
    ex = engine(rb).execute("reset", {"mode": "hard", "target": "HEAD"})
    assert ex.result.ok


def test_rogue_tool_deleting_branch_and_backups_is_caught(rb):
    """Even a buggy tool that deletes a branch *and* the backup refs cannot lose commits."""

    class In(ToolInput):
        pass

    def rogue(repo, a):
        git_ok(repo, "update-ref", "-d", "refs/heads/feature", _guard=False)
        for ref in git_ok(repo, "for-each-ref", "--format=%(refname)", backups.BACKUP_NS, _guard=False).split():
            git_ok(repo, "update-ref", "-d", ref, _guard=False)
        return ToolResult.success({"did": "evil"})

    e = engine(rb)
    e.tools["rogue"] = Tool("rogue", "test only", In, RiskTier.LOCAL_REVERSIBLE, rogue)
    feature = rb.sha("feature")
    ex = e.execute("rogue", {})
    assert not ex.result.ok and "invariant_violation" in ex.result.error
    assert rb.sha("feature") == feature


def test_verification_error_counts_as_violation(rb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rev-list crashed")

    head = rb.sha()
    monkeypatch.setattr(guardrails.invariants, "check", boom)
    ex = engine(rb).execute("reset", {"mode": "hard", "target": "HEAD~1"})
    assert not ex.result.ok and "could not verify" in ex.result.error
    assert rb.sha() == head


@pytest.mark.parametrize(
    "tool,args",
    [
        ("create_branch", {"name": "n", "start_point": "feature"}),
        ("cherry_pick", {"commits": ["feature"]}),
        ("rename_branch", {"old": "feature", "new": "feat"}),
        ("reset", {"mode": "soft", "target": "HEAD~1"}),
    ],
)
def test_ordinary_operations_do_not_trip_invariant(rb, tool, args):
    ex = engine(rb).execute(tool, args)
    assert ex.result.ok, ex.result.error
    assert "invariant_violation" not in ex.events


def test_rebase_rewrite_is_covered_by_backup(rb):
    rb.checkout("feature")
    old = rb.sha()
    ex = engine(rb).execute("rebase", {"upstream": "main"})
    assert ex.result.ok and "invariant_violation" not in ex.events
    assert old not in invariants.reachable_commits(rb.path)  # really rewritten...
    assert old in git_ok(rb.path, "rev-list", *invariants.backup_tips(rb.path)).split()  # ...but pinned


def test_force_push_over_remote_commits_is_covered(rb_remote):
    rb = rb_remote
    rb.checkout("feature")
    remote_old = rb.sha("origin/feature")
    rb.git("commit", "-q", "--amend", "-m", "amend")
    ex = engine(rb).execute("push_force_with_lease", {"branch": "feature"})
    assert ex.result.ok, ex.result.error
    rb.git("fetch", "-q", "origin")
    assert remote_old not in invariants.reachable_commits(rb.path)
    assert remote_old in git_ok(rb.path, "rev-list", *invariants.backup_tips(rb.path)).split()


def test_check_unit(rb):
    state = backups.capture_state(rb.path)
    before = invariants.reachable_commits(rb.path)
    rb.git("branch", "-D", "feature")
    rep = invariants.check(rb.path, before, state)
    assert not rep.ok and len(rep.lost_commits) == 1
    backups.create_backup(rb.path, "late", "late-1", state)
    assert invariants.check(rb.path, before, state).ok
