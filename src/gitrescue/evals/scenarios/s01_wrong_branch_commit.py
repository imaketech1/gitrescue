from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo, publish


def setup(root):
    ctx = new_repo(root)
    rb = ctx.rb
    rb.commit("initial", {"README.md": "# shop\n", "app.py": "print('shop')\n"})
    rb.branch("feature/login")
    rb.checkout("feature/login")
    rb.commit("login scaffold", {"login.py": "def login(): ...\n"})
    rb.checkout("main")
    publish(ctx, "main", "feature/login")
    ctx.info["main_before"] = rb.sha()
    ctx.info["feature_before"] = rb.sha("feature/login")
    ctx.info["wrong"] = rb.commit("add login form", {"templates/login.html": "<form>login</form>\n"})
    return ctx


def check(ctx, run):
    feat = "feature/login"
    return Check.of(
        {
            "main back to its pre-mistake commit": ctx.sha("main") == ctx.info["main_before"],
            "feature/login keeps its old history": ctx.ok("merge-base", "--is-ancestor", ctx.info["feature_before"], feat),
            "commit now on feature/login": "add login form" in ctx.subjects(feat, 3),
            "file content on feature/login": ctx.tree_file(feat, "templates/login.html") == "<form>login</form>\n",
            "no conflicts left": not ctx.in_progress(),
        }
    )


def reference(ctx):
    return [
        call("git_status"),
        call("git_log", {"max_count": 3}),
        call("checkout", {"target": "feature/login"}),
        call("cherry_pick", {"commits": [ctx.info["wrong"]]}),
        call("checkout", {"target": "main"}),
        call("reset", {"mode": "hard", "target": "origin/main"}),
        final("Moved 'add login form' to feature/login and reset main to origin/main."),
    ]


SCENARIO = Scenario(
    name="wrong_branch_commit",
    kind="standard",
    request="I committed to main instead of my feature branch feature/login. Move that commit over.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset"}),
    reference=reference,
)
