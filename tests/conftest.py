from __future__ import annotations

import pytest

from gitrescue.fixtures import RepoBuilder


@pytest.fixture
def rb(tmp_path) -> RepoBuilder:
    """A repo with three commits on main and a feature branch with one commit."""
    b = RepoBuilder(tmp_path / "repo")
    b.commit("initial", {"README.md": "hello\n"})
    b.commit("add app", {"app.py": "print('v1')\n"})
    b.branch("feature")
    b.checkout("feature")
    b.commit("feature work", {"feature.py": "x = 1\n"})
    b.checkout("main")
    b.commit("main work", {"app.py": "print('v2')\n"})
    return b


@pytest.fixture
def rb_remote(rb, tmp_path) -> RepoBuilder:
    rb.add_bare_remote(tmp_path / "remote.git")
    rb.push("main")
    rb.push("feature")
    return rb
