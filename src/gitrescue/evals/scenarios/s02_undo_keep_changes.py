from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo, publish

REFACTOR = "def total(items):\n    return sum(i.price for i in items)\n"


def setup(root):
    ctx = new_repo(root)
    rb = ctx.rb
    rb.commit("initial", {"cart.py": "def total(items):\n    t = 0\n    for i in items: t += i.price\n    return t\n"})
    publish(ctx, "main")
    ctx.info["base"] = rb.sha()
    rb.commit("WIP: half-done refactor", {"cart.py": REFACTOR})
    return ctx


def check(ctx, run):
    return Check.of(
        {
            "HEAD is the previous commit": ctx.sha("HEAD") == ctx.info["base"],
            "still on main": ctx.git("symbolic-ref", "--short", "HEAD").strip() == "main",
            "changes kept in working tree": ctx.file("cart.py") == REFACTOR,
        }
    )


def reference(ctx):
    return [call("git_log", {"max_count": 2}), call("reset", {"mode": "soft", "target": "HEAD~1"}), call("git_status"), final("Undid the commit; changes are staged.")]


SCENARIO = Scenario(
    name="undo_last_commit_keep_changes",
    kind="standard",
    request="Undo my last commit but keep the changes.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset"}),
    reference=reference,
)
