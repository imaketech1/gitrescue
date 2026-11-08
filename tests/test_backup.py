from __future__ import annotations

from gitrescue.git import run_git
from gitrescue.policy.backup import (
    BACKUP_NS,
    capture_state,
    create_backup,
    list_backups,
    load_backup,
    restore,
)


def test_snapshot_does_not_touch_index_or_worktree(rb):
    rb.write("app.py", "dirty\n")
    rb.write("new.txt", "untracked\n")
    rb.write("staged.txt", "s\n")
    rb.git("add", "staged.txt")
    before = rb.git("status", "--porcelain")
    s = capture_state(rb.path)
    assert rb.git("status", "--porcelain") == before
    assert s.head_ref == "refs/heads/main" and set(s.branches) == {"main", "feature"}
    assert s.index_tree and s.worktree_tree and s.index_tree != s.worktree_tree


def test_backup_pins_refs_and_persists(rb):
    b = create_backup(rb.path, "test", "bk-1")
    refs = rb.git("for-each-ref", "--format=%(refname)", BACKUP_NS).split()
    assert f"{BACKUP_NS}/bk-1/heads/main" in refs and f"{BACKUP_NS}/bk-1/heads/feature" in refs
    assert f"{BACKUP_NS}/bk-1/worktree" in refs
    assert load_backup(rb.path, "bk-1").state == b.state
    assert [x.id for x in list_backups(rb.path)] == ["bk-1"]


def test_restore_after_reset_hard_with_dirty_tree(rb):
    rb.write("app.py", "uncommitted edit\n")
    rb.write("notes.txt", "untracked notes\n")
    rb.write("staged.txt", "staged\n")
    rb.git("add", "staged.txt")
    head = rb.sha()
    status = rb.git("status", "--porcelain")
    create_backup(rb.path, "before", "bk-2")
    # disaster
    rb.git("reset", "--hard", "HEAD~2")
    (rb.path / "notes.txt").unlink()
    rb.write("junk.txt", "created after backup\n")
    rb.git("branch", "-f", "feature", "HEAD")
    out = restore(rb.path, "bk-2")
    assert rb.sha() == head
    assert rb.read("app.py") == "uncommitted edit\n"
    assert rb.read("notes.txt") == "untracked notes\n"
    assert not (rb.path / "junk.txt").exists()
    assert rb.git("status", "--porcelain") == status
    assert rb.sha("feature") != rb.sha("HEAD~2")
    assert out["safety_backup"].startswith("pre-restore")
    # the safety backup captured the junk file, so even that is recoverable
    safety = load_backup(rb.path, out["safety_backup"])
    assert "junk.txt" in rb.git("ls-tree", "--name-only", safety.state.worktree_tree).split()


def test_restore_detached_head_and_stash(rb):
    rb.write("app.py", "stash me\n")
    rb.git("stash", "push", "-m", "wip")
    rb.checkout("HEAD~1")
    create_backup(rb.path, "detached", "bk-3")
    rb.checkout("main")
    rb.git("stash", "drop")
    restore(rb.path, "bk-3")
    assert run_git(rb.path, "symbolic-ref", "-q", "HEAD").ok is False
    assert rb.sha() == rb.sha("main~1")
    assert "wip" in rb.git("stash", "list")


def test_restore_recreates_deleted_and_removes_new_branches(rb):
    create_backup(rb.path, "x", "bk-4")
    feature = rb.sha("feature")
    rb.git("branch", "-D", "feature")
    rb.git("branch", "extra", "main")  # fully contained in restored state -> removed
    rb.git("checkout", "-q", "-b", "wip")
    rb.commit("new work", {"w.txt": "w"})  # not in restored state -> kept
    restore(rb.path, "bk-4")
    branches = rb.git("branch", "--format=%(refname:short)").split()
    assert rb.sha("feature") == feature
    assert "extra" not in branches and "wip" in branches
    assert rb.git("symbolic-ref", "--short", "HEAD").strip() == "main"


def test_restore_aborts_in_progress_merge(rb):
    create_backup(rb.path, "x", "bk-5")
    rb.git("checkout", "-q", "-b", "other", "main~1")
    rb.commit("conflict", {"app.py": "other\n"})
    run_git(rb.path, "merge", "main", _guard=False)
    restore(rb.path, "bk-5")
    assert not (rb.path / ".git" / "MERGE_HEAD").exists()
    assert rb.read("app.py") == "print('v2')\n"
