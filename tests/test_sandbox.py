from __future__ import annotations

import pytest

from gitrescue.agent.llm import ScriptedLLM, call, final
from gitrescue.config import Settings
from gitrescue.git import run_git
from gitrescue.policy.backup import restore
from gitrescue.policy.guardrails import PolicyEngine, approve_all, deny_all
from gitrescue.runner import run_fix
from gitrescue.sandbox import Sandbox, SandboxError
from gitrescue.tracing import Tracer


def sb_engine(sb, approval=approve_all):
    return PolicyEngine(sb.path, approval=approval, run_id="sb", deferred_pushes=sb.deferred_pushes)


def test_sandbox_is_a_full_copy_and_isolated(rb):
    rb.write("app.py", "dirty\n")
    rb.write("untracked.txt", "u\n")
    rb.git("stash", "push", "-q", "-m", "s1", "--", "app.py")
    rb.write("app.py", "dirty again\n")
    rb.git("reset", "-q", "--hard", "HEAD~1")
    rb.write("app.py", "dirty again\n")
    with Sandbox(rb.path) as sb:
        assert sb.path != rb.path
        assert (sb.path / "untracked.txt").read_text() == "u\n"
        assert (sb.path / "app.py").read_text() == "dirty again\n"
        assert "s1" in run_git(sb.path, "stash", "list").stdout
        assert "reset: moving to HEAD~1" in run_git(sb.path, "reflog").stdout
        e = sb_engine(sb)
        e.execute("reset", {"mode": "hard", "target": "HEAD~1"})
        assert rb.read("app.py") == "dirty again\n"  # real repo untouched
        assert rb.sha("main") != run_git(sb.path, "rev-parse", "main").stdout.strip()
    assert not sb.path.exists()


def test_apply_moves_branches_and_is_reversible(rb):
    before_main = rb.sha("main")
    with Sandbox(rb.path) as sb:
        e = sb_engine(sb)
        assert e.execute("create_branch", {"name": "rescued", "start_point": "main"}).result.ok
        assert e.execute("reset", {"mode": "hard", "target": "HEAD~1"}).result.ok
        plan = sb.plan()
        kinds = {b.name: b.kind for b in plan.branches}
        assert kinds == {"rescued": "created", "main": "moved"}
        main_change = next(b for b in plan.branches if b.name == "main")
        assert any("main work" in c for c in main_change.commits_removed)
        res = sb.apply("run1")
    assert rb.sha("rescued") == before_main and rb.sha("main") == rb.sha("rescued~1")
    assert rb.read("app.py") == "print('v1')\n"
    assert not rb.git("for-each-ref", "refs/gitrescue/sandbox").strip()  # staging refs cleaned up
    restore(rb.path, res["backup_id"])
    assert rb.sha("main") == before_main


def test_apply_syncs_worktree_index_and_stash(rb):
    rb.write("app.py", "edit\n")
    rb.write("notes.txt", "keep me\n")
    with Sandbox(rb.path) as sb:
        e = sb_engine(sb)
        assert e.execute("stash", {"action": "push", "message": "parked", "include_untracked": False}).result.ok
        assert e.execute("stage", {"paths": ["notes.txt"]}).result.ok
        sb.apply("run2")
    assert rb.read("app.py") == "print('v2')\n"
    assert "parked" in rb.git("stash", "list")
    assert rb.git("diff", "--cached", "--name-only").split() == ["notes.txt"]
    assert rb.read("notes.txt") == "keep me\n"


def test_apply_rename_removes_old_branch(rb):
    with Sandbox(rb.path) as sb:
        assert sb_engine(sb).execute("rename_branch", {"old": "feature", "new": "feat/login"}).result.ok
        sb.apply("run3")
    branches = rb.git("branch", "--format=%(refname:short)").split()
    assert "feat/login" in branches and "feature" not in branches


def test_apply_refuses_if_real_repo_changed(rb):
    with Sandbox(rb.path) as sb:
        sb_engine(sb).execute("create_branch", {"name": "x"})
        rb.commit("concurrent work", {"c.txt": "c"})
        head = rb.sha()
        with pytest.raises(SandboxError, match="changed while"):
            sb.apply("run4")
    assert rb.sha() == head and "x" not in rb.git("branch")


def test_apply_refuses_mid_merge(rb):
    rb.git("checkout", "-q", "-b", "o", "main~1")
    rb.commit("o", {"app.py": "o\n"})
    with Sandbox(rb.path) as sb:
        run_git(sb.path, "merge", "main", _guard=False)
        plan = sb.plan()
        assert plan.blocking_issue and "merge" in plan.blocking_issue
        with pytest.raises(SandboxError):
            sb.apply("run5")


def test_sandbox_cannot_push_directly(rb_remote):
    with Sandbox(rb_remote.path) as sb:
        res = run_git(sb.path, "push", "origin", "main")
        assert not res.ok
        assert run_git(sb.path, "fetch", "origin").ok  # fetch still works


def test_deferred_push_only_after_apply_and_approval(rb_remote):
    rb = rb_remote
    rb.checkout("feature")
    remote_before = rb.git("ls-remote", "origin", "refs/heads/feature").split()[0]
    for approval, expect_pushed in ((deny_all, False), (approve_all, True)):
        with Sandbox(rb.path) as sb:
            e = sb_engine(sb)
            assert e.execute("reset", {"mode": "soft", "target": "HEAD~1"}).result.ok
            assert e.execute("commit", {"message": "reworded feature"}).result.ok
            ex = e.execute("push_force_with_lease", {"branch": "feature"})
            assert ex.result.ok and ex.result.data["deferred"] and "deferred" in ex.events
            assert rb.git("ls-remote", "origin", "refs/heads/feature").split()[0] == remote_before
            real = PolicyEngine(rb.path, approval=approval, run_id="real")
            res = sb.apply("run6", push_executor=lambda a: real.execute("push_force_with_lease", a))
        remote_now = rb.git("ls-remote", "origin", "refs/heads/feature").split()[0]
        assert res["pushes"][0]["ok"] is expect_pushed
        assert (remote_now == rb.sha("feature")) is expect_pushed
        if not expect_pushed:
            assert remote_now == remote_before
            restore(rb.path, res["backup_id"])


def test_deferred_push_to_main_still_blocked_in_sandbox(rb_remote):
    with Sandbox(rb_remote.path) as sb:
        ex = sb_engine(sb).execute("push_force_with_lease", {"branch": "main"})
        assert ex.result.blocked and sb.deferred_pushes == []


def test_relative_remote_url_is_made_absolute(rb, tmp_path):
    rb.add_bare_remote(tmp_path / "rel.git")
    rb.git("remote", "set-url", "origin", "../rel.git")
    rb.push("main")
    with Sandbox(rb.path) as sb:
        url = run_git(sb.path, "remote", "get-url", "origin").stdout.strip()
        assert url == str((tmp_path / "rel.git").resolve())


def _runner(rb, steps, confirm, sandbox=True):
    s = Settings(trace_dir=rb.path.parent / "traces")
    return run_fix("move my last commit to a new branch", rb.path, ScriptedLLM(steps), s, Tracer(s.trace_dir),
                   approve_all, confirm, sandbox=sandbox)


STEPS = [
    call("create_branch", {"name": "moved", "start_point": "HEAD"}),
    call("reset", {"mode": "hard", "target": "HEAD~1"}),
    final("Moved."),
]


def test_run_fix_not_applied_without_confirmation(rb):
    head = rb.sha()
    seen = []
    out = _runner(rb, list(STEPS), lambda plan: seen.append(plan) or False)
    assert out.sandboxed and not out.applied and seen and not seen[0].empty
    assert rb.sha() == head and "moved" not in rb.git("branch")


def test_run_fix_applied_with_confirmation(rb):
    head = rb.sha()
    out = _runner(rb, list(STEPS), lambda plan: True)
    assert out.applied and rb.sha("moved") == head and rb.sha("main") == rb.sha("moved~1")


def test_run_fix_read_only_has_empty_plan(rb):
    asked = []
    out = _runner(rb, [call("git_status"), final("all good")], lambda p: asked.append(p) or True)
    assert out.plan.empty and not asked and not out.applied


def test_run_fix_no_sandbox_works_in_place(rb):
    head = rb.sha()
    out = _runner(rb, list(STEPS), lambda p: False, sandbox=False)
    assert not out.sandboxed and rb.sha("moved") == head
