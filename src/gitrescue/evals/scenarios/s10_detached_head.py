from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"model.py": "LR = 0.1\n"})
    rb.commit("baseline", {"model.py": "LR = 0.01\n"})
    rb.checkout(rb.sha("HEAD~1"))
    rb.commit("experiment A", {"model.py": "LR = 0.5\n"})
    ctx.info["lost"] = rb.commit("experiment B", {"notes.md": "A beats baseline\n"})
    rb.checkout("main")
    ctx.info["main"] = rb.sha("main")
    return ctx


def check(ctx, run):
    return Check.of(
        {
            "experiment branch at lost commit": ctx.sha("experiment") == ctx.info["lost"],
            "main untouched": ctx.sha("main") == ctx.info["main"],
        }
    )


def reference(ctx):
    return [call("git_reflog", {"max_count": 10}), call("create_branch", {"name": "experiment", "start_point": ctx.info["lost"]}), final("Saved on 'experiment'.")]


SCENARIO = Scenario(
    name="detached_head_recovery",
    kind="standard",
    request="I made some commits while in detached HEAD and then switched back to main. Now I can't find them. Save them on a branch called experiment.",
    setup=setup,
    check=check,
    reference=reference,
)
