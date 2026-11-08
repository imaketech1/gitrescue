from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"search.py": ""})
    rb.git("checkout", "-q", "-b", "search")
    ctx.info["base"] = rb.sha()
    rb.commit("wip 1", {"search.py": "def search(q):\n"})
    rb.commit("wip 2", {"search.py": "def search(q):\n    return []\n"})
    rb.commit("wip 3", {"index.py": "INDEX = {}\n"})
    ctx.info["tree"] = rb.git("rev-parse", "HEAD^{tree}").strip()
    return ctx


def check(ctx, run):
    return Check.of(
        {
            "exactly one commit on top of base": ctx.sha("search~1") == ctx.info["base"],
            "message": ctx.subjects("search", 1) == ["Add search feature"],
            "content unchanged": ctx.git("rev-parse", "search^{tree}").strip() == ctx.info["tree"],
            "clean tree": ctx.git("status", "--porcelain").strip() == "",
        }
    )


def reference(ctx):
    return [
        call("git_log", {"max_count": 4}),
        call("reset", {"mode": "soft", "target": "HEAD~3"}),
        call("commit", {"message": "Add search feature"}),
        final("Squashed."),
    ]


SCENARIO = Scenario(
    name="squash_last_n_commits",
    kind="standard",
    request="Squash my last 3 commits into a single commit with the message 'Add search feature'.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset", "rebase"}),
    reference=reference,
)
