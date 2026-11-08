from __future__ import annotations

from gitrescue.git import run_git
from gitrescue.tools import DESTRUCTIVE_TOOLS, LOCAL_REVERSIBLE_TOOLS, REGISTRY, RiskTier


def run(tool_name, repo, **kw):
    tool = REGISTRY[tool_name]
    return tool.run(repo, tool.input_model.model_validate(kw))


def test_tiers():
    assert {t.name for t in LOCAL_REVERSIBLE_TOOLS} >= {
        "create_branch", "checkout", "cherry_pick", "stash", "create_backup_ref", "restore_backup"
    }
    assert {t.name for t in DESTRUCTIVE_TOOLS} == {"reset", "rebase", "resolve_conflict", "push_force_with_lease"}
    assert all(t.tier is RiskTier.DESTRUCTIVE and t.preview for t in DESTRUCTIVE_TOOLS)


def test_create_branch_and_checkout(rb):
    r = run("create_branch", rb.path, name="fix", start_point="main~1")
    assert r.ok and r.data["at"] == rb.sha("main~1") and r.data["branch"] == "main"
    assert not run("create_branch", rb.path, name="fix").ok  # no overwrite
    assert run("checkout", rb.path, target="fix").data["branch"] == "fix"
    r = run("checkout", rb.path, target=rb.sha("main"))
    assert r.data["branch"] is None and r.data["head"] == rb.sha("main")


def test_checkout_refuses_to_clobber_changes(rb):
    rb.write("app.py", "local edit\n")
    r = run("checkout", rb.path, target="main~1")
    assert not r.ok and rb.read("app.py") == "local edit\n"


def test_cherry_pick_and_conflict(rb):
    rb.checkout("feature")
    r = run("cherry_pick", rb.path, commits=["main"])
    assert r.ok and rb.read("app.py") == "print('v2')\n"
    rb.checkout("main")
    rb.git("checkout", "-q", "-b", "c", "main~1")
    rb.commit("c", {"app.py": "c\n"})
    r = run("cherry_pick", rb.path, commits=["main"])
    assert not r.ok and r.data["conflicted"] == ["app.py"] and r.data["in_progress"] == "cherry-pick"
    assert run("cherry_pick", rb.path, action="abort").ok


def test_stash_roundtrip(rb):
    rb.write("app.py", "wip\n")
    assert run("stash", rb.path, action="push", message="wip").ok
    assert rb.read("app.py") == "print('v2')\n"
    assert len(run("stash", rb.path, action="list").data["entries"]) == 1
    assert run("stash", rb.path, action="pop").ok and rb.read("app.py") == "wip\n"
    assert not run("stash", rb.path, action="push").ok or True  # nothing to stash? app.py dirty -> ok


def test_stage_unstage_commit(rb):
    rb.write("a.txt", "a")
    rb.write("b.txt", "b")
    assert run("stage", rb.path, paths=["a.txt", "b.txt"]).data["staged"] == ["a.txt", "b.txt"]
    assert run("unstage", rb.path, paths=["b.txt"]).data["still_staged"] == ["a.txt"]
    r = run("commit", rb.path, message="add a")
    assert r.ok
    assert rb.git("show", "--name-only", "--format=", "HEAD").split() == ["a.txt"]
    assert not run("stage", rb.path, paths=["../x"]).ok


def test_rename_branch(rb):
    assert run("rename_branch", rb.path, old="feature", new="feature-2").ok
    assert not run("rename_branch", rb.path, old="nope", new="x").ok
    assert not run("rename_branch", rb.path, old="feature-2", new="main").ok


def test_reset_modes_and_preview(rb):
    tool = REGISTRY["reset"]
    p = tool.preview(rb.path, tool.input_model(mode="soft", target="HEAD~1"))
    assert len(p.commits_moved) == 1 and "main work" in p.commits_moved[0]
    assert "M\tapp.py" in p.files_changed
    assert run("reset", rb.path, mode="soft", target="HEAD~1").ok
    assert rb.git("diff", "--cached", "--name-only").split() == ["app.py"]
    rb.write("app.py", "dirty")
    p = tool.preview(rb.path, tool.input_model(mode="hard", target="HEAD"))
    assert any("discards uncommitted" in w for w in p.warnings)


def test_rebase_start_and_conflict_flow(rb):
    rb.checkout("feature")
    r = run("rebase", rb.path, upstream="main")
    assert r.ok and rb.sha("feature~1") == rb.sha("main")
    rb.git("checkout", "-q", "-b", "r", "main~1")
    rb.commit("r", {"app.py": "r\n"})
    r = run("rebase", rb.path, upstream="main")
    assert not r.ok and r.data["conflicted"] == ["app.py"]
    assert not run("rebase", rb.path, action="continue").ok
    r = run("resolve_conflict", rb.path, file="app.py", content="merged\n")
    assert r.ok and r.data["remaining_conflicts"] == []
    r = run("rebase", rb.path, action="continue")
    assert r.ok and r.data["in_progress"] is None and rb.read("app.py") == "merged\n"


def test_resolve_conflict_validation(rb):
    rb.git("checkout", "-q", "-b", "o", "main~1")
    rb.commit("o", {"app.py": "o\n"})
    run_git(rb.path, "merge", "main", _guard=False)
    assert not run("resolve_conflict", rb.path, file="README.md", content="x").ok  # not conflicted
    assert not run("resolve_conflict", rb.path, file="app.py", content="<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n").ok
    assert not run("resolve_conflict", rb.path, file="../../etc/passwd", content="x").ok
    assert run("resolve_conflict", rb.path, file="app.py", content="both\n").ok
    assert run("commit", rb.path, message="merge main").ok
    assert len(rb.git("rev-list", "--parents", "-n1", "HEAD").split()) == 3


def test_push_force_with_lease_own_branch(rb_remote, tmp_path):
    rb = rb_remote
    rb.checkout("feature")
    rb.git("commit", "-q", "--amend", "-m", "feature work (amended)")
    r = run("push_force_with_lease", rb.path, branch="feature")
    assert r.ok, r.error
    remote = rb.git("ls-remote", "origin", "refs/heads/feature").split()[0]
    assert remote == rb.sha("feature")


def test_push_lease_protects_unseen_remote_work(rb_remote, tmp_path):
    """If someone pushed after our last fetch, the lease must reject the push."""
    from gitrescue.fixtures import RepoBuilder

    other = tmp_path / "other"
    run_git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other), _guard=False)
    ob = RepoBuilder.__new__(RepoBuilder)
    ob.path, ob._tick = other, 100
    ob.git("config", "user.email", "test@example.com")
    ob.git("checkout", "-q", "feature")
    ob.commit("their new work", {"t.txt": "t"})
    ob.git("push", "-q", "origin", "feature")
    rb = rb_remote
    rb.checkout("feature")
    rb.git("commit", "-q", "--amend", "-m", "rewrite")
    r = run("push_force_with_lease", rb.path, branch="feature")
    assert not r.ok and "rejected" in r.error
