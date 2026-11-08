from __future__ import annotations

from gitrescue.tools import READ_TOOLS, REGISTRY, RiskTier


def run(tool_name, repo, **kw):
    tool = REGISTRY[tool_name]
    return tool.run(repo, tool.input_model.model_validate(kw))


def test_all_read_tools_are_read_tier():
    assert {t.name for t in READ_TOOLS} == {
        "git_status", "git_log", "git_reflog", "git_diff", "list_branches", "show_commit", "list_backups"
    }
    assert all(t.tier is RiskTier.READ for t in READ_TOOLS)


def test_schemas_are_openai_function_shaped():
    for t in READ_TOOLS:
        s = t.schema()
        assert s["type"] == "function"
        assert s["function"]["name"] == t.name
        assert s["function"]["parameters"]["type"] == "object"


def test_status_clean_and_dirty(rb):
    r = run("git_status", rb.path)
    assert r.ok and r.data["branch"] == "main" and not r.data["detached"]
    assert r.data["staged"] == r.data["unstaged"] == r.data["untracked"] == []
    rb.write("app.py", "changed\n")
    rb.write("new.txt", "n\n")
    rb.write("staged.txt", "s\n")
    rb.git("add", "staged.txt")
    r = run("git_status", rb.path)
    assert {"path": "app.py", "status": "M"} in r.data["unstaged"]
    assert {"path": "staged.txt", "status": "A"} in r.data["staged"]
    assert r.data["untracked"] == ["new.txt"]


def test_status_detached_and_upstream(rb_remote):
    rb = rb_remote
    rb.commit("ahead", {"a.txt": "a"})
    r = run("git_status", rb.path)
    assert r.data["upstream"] == "origin/main" and r.data["ahead"] == 1 and r.data["behind"] == 0
    rb.checkout("HEAD~1")
    assert run("git_status", rb.path).data["detached"] is True


def test_status_reports_conflict(rb):
    from gitrescue.git import run_git

    rb.git("checkout", "-q", "-b", "other", "main~1")
    rb.commit("other app", {"app.py": "print('other')\n"})
    run_git(rb.path, "merge", "main", _guard=False)
    r = run("git_status", rb.path)
    assert r.data["in_progress"] == "merge"
    assert r.data["conflicted"] == ["app.py"]


def test_log(rb):
    r = run("git_log", rb.path, max_count=2)
    assert [c["subject"] for c in r.data["commits"]] == ["main work", "add app"]
    r = run("git_log", rb.path, ref="feature", exclude_ref="main")
    assert [c["subject"] for c in r.data["commits"]] == ["feature work"]
    r = run("git_log", rb.path, all_refs=True)
    assert {c["subject"] for c in r.data["commits"]} >= {"feature work", "main work"}


def test_log_rejects_option_injection(rb):
    r = run("git_log", rb.path, ref="--output=/tmp/pwned")
    assert not r.ok and "invalid" in r.error


def test_reflog_finds_lost_commit(rb):
    lost = rb.sha("HEAD")
    rb.git("reset", "-q", "--hard", "HEAD~1")
    r = run("git_reflog", rb.path)
    assert r.ok
    assert lost in [e["sha"] for e in r.data["entries"]]


def test_diff_variants(rb):
    rb.write("app.py", "print('v3')\n")
    r = run("git_diff", rb.path)
    assert r.data["files"][0]["path"] == "app.py" and "v3" in r.data["patch"]
    assert run("git_diff", rb.path, staged=True).data["files"] == []
    r = run("git_diff", rb.path, from_ref="main~1", to_ref="main", stat_only=True)
    assert r.data["files"] == [{"path": "app.py", "added": 1, "deleted": 1}] and "patch" not in r.data


def test_diff_truncates_huge_patch(rb):
    rb.write("big.txt", "line\n" * 50_000)
    rb.git("add", "big.txt")
    r = run("git_diff", rb.path, staged=True)
    assert r.data["truncated"] is True
    assert len(r.data["patch"]) < 13_000


def test_diff_rejects_outside_paths(rb):
    r = run("git_diff", rb.path, paths=["../../etc/passwd"])
    assert not r.ok and "outside" in r.error


def test_list_branches(rb_remote):
    r = run("list_branches", rb_remote.path)
    names = {b["name"]: b for b in r.data["local"]}
    assert names["main"]["current"] and names["main"]["upstream"] == "origin/main"
    assert {b["name"] for b in r.data["remote"]} == {"origin/main", "origin/feature"}


def test_show_commit(rb):
    r = run("show_commit", rb.path, ref="feature")
    assert r.data["message"] == "feature work"
    assert r.data["files"] == [{"path": "feature.py", "added": 1, "deleted": 0}]
    assert "x = 1" in r.data["patch"]


def test_unknown_ref_is_structured_error(rb):
    r = run("show_commit", rb.path, ref="nope")
    assert r.ok is False and r.error and "unknown revision" in r.error
