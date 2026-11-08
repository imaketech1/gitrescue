from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo, publish
from gitrescue.git import git_ok

TEAMMATE = {
    "GIT_AUTHOR_NAME": "Teammate",
    "GIT_AUTHOR_EMAIL": "teammate@example.com",
    "GIT_COMMITTER_NAME": "Teammate",
    "GIT_COMMITTER_EMAIL": "teammate@example.com",
}


def setup(root):
    ctx = new_repo(root)
    rb = ctx.rb
    rb.commit("initial", {"docs.md": "# docs\n"})
    rb.git("checkout", "-q", "-b", "docs")
    rb.commit("outline", {"docs.md": "# docs\n## intro\n"})
    publish(ctx, "main", "docs")
    other = root / "teammate"
    git_ok(root, "clone", "-q", "-b", "docs", str(ctx.remote), str(other), _guard=False)
    (other / "faq.md").write_text("# FAQ\n")
    git_ok(other, "add", "faq.md", _guard=False)
    git_ok(other, "commit", "-q", "-m", "add FAQ", env=TEAMMATE, _guard=False)
    git_ok(other, "push", "-q", "origin", "docs", _guard=False)
    ctx.info["remote_tip"] = git_ok(other, "rev-parse", "HEAD", _guard=False).strip()
    rb.commit("add usage section", {"docs.md": "# docs\n## intro\n## usage\n"})
    rb.git("fetch", "-q", "origin")
    return ctx


def check(ctx, run):
    merges = ctx.git("rev-list", "--merges", "origin/docs..docs").strip()
    return Check.of(
        {
            "contains remote work": ctx.ok("merge-base", "--is-ancestor", ctx.info["remote_tip"], "docs"),
            "local commit on top": ctx.subjects("docs", 1) == ["add usage section"],
            "exactly one local commit ahead": ctx.git("rev-list", "--count", "origin/docs..docs").strip() == "1",
            "no merge commits": merges == "",
            "files from both sides": ctx.tree_file("docs", "faq.md") == "# FAQ\n" and "## usage" in (ctx.tree_file("docs", "docs.md") or ""),
            "remote untouched": ctx.remote_sha("docs") == ctx.info["remote_tip"],
        }
    )


def reference(ctx):
    return [call("git_status"), call("rebase", {"upstream": "origin/docs"}), final("Rebased onto origin/docs.")]


SCENARIO = Scenario(
    name="diverged_local_remote",
    kind="standard",
    request="My branch and origin have diverged. Bring in the remote changes and keep my local commit on top, without a merge commit.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"rebase", "reset"}),
    reference=reference,
)
