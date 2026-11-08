"""Post-step invariant: no previously reachable commit (and no uncommitted work)
may become unreachable unless a GitRescue backup still holds it.

"Reachable" here deliberately excludes backup refs: it means reachable from
branches, tags, remote-tracking refs, HEAD and stash entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitrescue.git import git_ok, run_git
from gitrescue.policy.backup import BACKUP_NS, RepoState


def _stash_shas(repo: Path) -> list[str]:
    if not run_git(repo, "rev-parse", "--verify", "--quiet", "refs/stash").ok:
        return []
    return git_ok(repo, "reflog", "show", "--format=%H", "refs/stash").split()


def reachable_commits(repo: Path) -> set[str]:
    tips = ["--branches", "--tags", "--remotes"]
    if run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD").ok:
        tips.append("HEAD")
    tips += _stash_shas(repo)
    return set(git_ok(repo, "rev-list", *tips).split())


def backup_tips(repo: Path) -> list[str]:
    return sorted(set(git_ok(repo, "for-each-ref", "--format=%(objectname)", BACKUP_NS).split()))


def pinned_worktree_trees(repo: Path) -> set[str]:
    out = git_ok(repo, "for-each-ref", "--format=%(refname)\x1f%(tree)", BACKUP_NS)
    trees = set()
    for line in out.splitlines():
        ref, _, tree = line.partition("\x1f")
        if ref.endswith("/worktree") and tree:
            trees.add(tree)
    return trees


@dataclass
class InvariantReport:
    ok: bool
    lost_commits: list[str] = field(default_factory=list)
    lost_worktree: bool = False

    def describe(self) -> str:
        parts = []
        if self.lost_commits:
            parts.append(f"{len(self.lost_commits)} commit(s) became unreachable without a backup: "
                         + ", ".join(c[:10] for c in self.lost_commits[:10]))
        if self.lost_worktree:
            parts.append("uncommitted changes were discarded without a backup")
        return "; ".join(parts) or "ok"


def check(repo: Path, before_reachable: set[str], before_state: RepoState) -> InvariantReport:
    after = reachable_commits(repo)
    lost = before_reachable - after
    uncovered: list[str] = []
    if lost:
        tips = backup_tips(repo)
        covered: set[str] = set()
        if tips:
            # commits among `lost` that are NOT reachable from any backup ref
            stdin = "".join(f"{c}\n" for c in sorted(lost)) + "".join(f"^{t}\n" for t in tips)
            out = git_ok(repo, "rev-list", "--stdin", input_text=stdin)
            not_covered = set(out.split()) & lost
            covered = lost - not_covered
        uncovered = sorted(lost - covered)
    lost_wt = False
    if before_state.worktree_tree and _had_uncommitted_work(repo, before_state):
        from gitrescue.policy.backup import worktree_tree

        if worktree_tree(repo) != before_state.worktree_tree:
            lost_wt = before_state.worktree_tree not in pinned_worktree_trees(repo)
    return InvariantReport(ok=not uncovered and not lost_wt, lost_commits=uncovered, lost_worktree=lost_wt)


def _had_uncommitted_work(repo: Path, state: RepoState) -> bool:
    if not state.head_sha:
        return True
    head_tree = git_ok(repo, "rev-parse", f"{state.head_sha}^{{tree}}").strip()
    return head_tree != state.worktree_tree or (state.index_tree is not None and state.index_tree != head_tree)
