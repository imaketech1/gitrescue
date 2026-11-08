"""Helpers for building throwaway git repositories (used by tests and eval scenarios)."""

from __future__ import annotations

from pathlib import Path

from gitrescue.git import git_ok

IDENTITY = {
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


class RepoBuilder:
    """Imperative builder for a scratch repo. Uses unguarded git: it is setup code, not agent code."""

    def __init__(self, path: Path, branch: str = "main"):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        git_ok(self.path, "init", "-q", "-b", branch, _guard=False)
        for k, v in (
            ("user.name", "Test User"),
            ("user.email", "test@example.com"),
            ("commit.gpgsign", "false"),
            ("core.autocrlf", "false"),
        ):
            git_ok(self.path, "config", k, v, _guard=False)
        self._tick = 0

    def git(self, *args: str) -> str:
        self._tick += 1
        env = {**IDENTITY, "GIT_AUTHOR_DATE": f"2026-01-01T00:{self._tick // 60:02d}:{self._tick % 60:02d}Z"}
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"]
        return git_ok(self.path, *args, env=env, _guard=False)

    def write(self, rel: str, content: str) -> Path:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def read(self, rel: str) -> str:
        return (self.path / rel).read_text()

    def commit(self, message: str, files: dict[str, str] | None = None) -> str:
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.sha("HEAD")

    def sha(self, rev: str = "HEAD") -> str:
        return self.git("rev-parse", "--verify", rev).strip()

    def branch(self, name: str, start: str = "HEAD") -> None:
        self.git("branch", name, start)

    def checkout(self, target: str) -> None:
        self.git("checkout", "-q", target)

    def add_bare_remote(self, remote_path: Path, name: str = "origin") -> Path:
        remote_path = Path(remote_path)
        git_ok(remote_path.parent, "init", "-q", "--bare", str(remote_path), _guard=False)
        git_ok(remote_path, "symbolic-ref", "HEAD", "refs/heads/main", _guard=False)
        self.git("remote", "add", name, str(remote_path))
        return remote_path

    def push(self, *refspecs: str, remote: str = "origin", upstream: bool = True) -> None:
        args = ["push", "-q"]
        if upstream:
            args.append("-u")
        self.git(*args, remote, *refspecs)
