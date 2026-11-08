from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"api.py": "routes = []\n"})
    rb.git("checkout", "-q", "-b", "payments")
    rb.commit("add payment model", {"models.py": "class Payment: ...\n"})
    ctx.info["c1"] = rb.commit("add payment api", {"api.py": "routes = ['/pay']\n"})
    ctx.info["c2"] = rb.commit("add payment tests", {"test_api.py": "def test_pay(): ...\n"})
    rb.git("reset", "-q", "--hard", "HEAD~2")
    return ctx


def check(ctx, run):
    return Check.of(
        {
            "payments tip is the lost commit": ctx.sha("payments") == ctx.info["c2"],
            "both lost commits on payments": ctx.ok("merge-base", "--is-ancestor", ctx.info["c1"], "payments"),
            "files restored": ctx.tree_file("payments", "test_api.py") == "def test_pay(): ...\n",
        }
    )


def reference(ctx):
    return [call("git_reflog", {"max_count": 10}), call("reset", {"mode": "hard", "target": ctx.info["c2"]}), final("Recovered both commits.")]


SCENARIO = Scenario(
    name="recover_after_reset_hard",
    kind="standard",
    request="I ran git reset --hard by mistake and lost my last two commits on this branch. Get them back.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset"}),
    reference=reference,
)
