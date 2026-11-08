"""The low-level guard must refuse dangerous git invocations regardless of caller."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gitrescue.git import GitBlocked, guard_git_args, resolve_repo_path, run_git, validate_branch_name, validate_rev

BLOCKED = [
    ["clean", "-fdx"],
    ["clean", "-n"],
    ["config", "user.email", "evil@x"],
    ["config", "--unset", "user.email"],
    ["config", "--get", "x", "--add", "y", "z"],
    ["config", "--global", "core.sshCommand", "sh"],
    ["credential", "fill"],
    ["credential-store", "get"],
    ["-c", "core.pager=sh", "log"],
    ["--git-dir=/etc", "status"],
    ["gc", "--prune=now"],
    ["prune"],
    ["reflog", "expire", "--expire=now", "--all"],
    ["reflog", "delete", "HEAD@{1}"],
    ["filter-branch", "--all"],
    ["remote", "set-url", "origin", "http://evil"],
    ["remote", "remove", "origin"],
    ["push", "--force", "origin", "feature"],
    ["push", "-f", "origin", "feature"],
    ["push", "origin", "+feature:feature"],
    ["push", "origin", ":feature"],
    ["push", "--delete", "origin", "feature"],
    ["push", "-d", "origin", "feature"],
    ["push", "--mirror", "origin"],
    ["push", "--all", "origin"],
    ["push", "--prune", "origin", "feature"],
    ["push", "--force-with-lease", "origin", "main"],
    ["push", "--force-with-lease", "origin", "HEAD:main"],
    ["push", "--force-with-lease", "origin", "feature:refs/heads/master"],
    ["push", "--force-with-lease", "origin"],
    ["push", "--no-verify", "origin", "feature"],
    ["push", "--receive-pack=sh", "origin", "feature"],
    ["branch", "-D", "-r", "origin/main"],
    ["update-ref", "-d", "refs/remotes/origin/main"],
    ["status\nclean"],
    [],
]


@pytest.mark.parametrize("args", BLOCKED, ids=lambda a: " ".join(a) or "<empty>")
def test_guard_blocks(args):
    with pytest.raises(GitBlocked):
        guard_git_args(args)


ALLOWED = [
    ["status"],
    ["config", "--get", "user.email"],
    ["push", "--force-with-lease", "origin", "feature"],
    ["push", "--force-with-lease=feature:abc", "origin", "feature:feature"],
    ["push", "origin", "main"],
    ["reflog", "show", "HEAD"],
    ["remote", "-v"],
    ["branch", "-m", "a", "b"],
]


@pytest.mark.parametrize("args", ALLOWED, ids=lambda a: " ".join(a))
def test_guard_allows(args):
    guard_git_args(args)


def test_run_git_enforces_guard(rb):
    (rb.path / "junk.txt").write_text("untracked")
    with pytest.raises(GitBlocked):
        run_git(rb.path, "clean", "-fd")
    assert (rb.path / "junk.txt").exists()


def test_validate_rev_rejects_options(rb):
    for bad in ["--all", "-n", "HEAD; rm -rf /", "a b", ""]:
        with pytest.raises(ValueError):
            validate_rev(rb.path, bad)
    assert len(validate_rev(rb.path, "HEAD~1")) == 40


def test_validate_branch_name(rb):
    for bad in ["-D", "a..b", "x y", "HEAD@{1}", "a:b", "~x"]:
        with pytest.raises(ValueError):
            validate_branch_name(rb.path, bad)
    assert validate_branch_name(rb.path, "feature/ok-1") == "feature/ok-1"


def test_resolve_repo_path_blocks_escape(rb):
    for bad in ["../outside.txt", "/etc/passwd", ".git/config", "sub/../../x", "."]:
        with pytest.raises(ValueError):
            resolve_repo_path(rb.path, bad)
    (rb.path.parent / "outside").mkdir()
    (rb.path / "link").symlink_to(rb.path.parent / "outside")
    with pytest.raises(ValueError):
        resolve_repo_path(rb.path, "link/file.txt")
    assert resolve_repo_path(rb.path, "app.py") == (rb.path / "app.py").resolve()


def test_tools_never_bypass_guard():
    """Static check: no tool module may call git with _guard=False."""
    tools_dir = Path(__file__).resolve().parents[1] / "src" / "gitrescue" / "tools"
    offenders = []
    for py in tools_dir.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "_guard":
                offenders.append(py.name)
    assert offenders == []
