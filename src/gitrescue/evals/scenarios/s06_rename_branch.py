from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"a.txt": "a\n"})
    rb.git("checkout", "-q", "-b", "fature/checkout")
    ctx.info["tip"] = rb.commit("checkout page", {"checkout.py": "pass\n"})
    return ctx


def check(ctx, run):
    branches = ctx.git("branch", "--format=%(refname:short)").split()
    return Check.of(
        {
            "new name exists at same commit": ctx.sha("feature/checkout") == ctx.info["tip"],
            "old name gone": "fature/checkout" not in branches,
            "main untouched": "main" in branches,
        }
    )


def reference(ctx):
    return [call("list_branches"), call("rename_branch", {"old": "fature/checkout", "new": "feature/checkout"}), final("Renamed.")]


SCENARIO = Scenario(
    name="rename_branch",
    kind="standard",
    request="I made a typo in my branch name: 'fature/checkout' should be 'feature/checkout'.",
    setup=setup,
    check=check,
    reference=reference,
)
