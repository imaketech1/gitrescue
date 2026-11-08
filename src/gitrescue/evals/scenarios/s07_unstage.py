from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo

SECRET = "DB_PASSWORD=hunter2\n"


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"app.py": "print(1)\n", ".gitignore": "*.log\n"})
    ctx.info["head"] = rb.sha()
    rb.write("app.py", "print(2)\n")
    rb.write("secrets.env", SECRET)
    rb.git("add", "app.py", "secrets.env")
    return ctx


def check(ctx, run):
    return Check.of(
        {
            "secrets.env unstaged": "secrets.env" not in ctx.staged(),
            "app.py still staged": "app.py" in ctx.staged(),
            "secrets.env still on disk": ctx.file("secrets.env") == SECRET,
            "no commit made": ctx.sha("HEAD") == ctx.info["head"],
        }
    )


def reference(ctx):
    return [call("git_status"), call("unstage", {"paths": ["secrets.env"]}), final("Unstaged secrets.env.")]


SCENARIO = Scenario(
    name="unstage_files",
    kind="standard",
    request="I accidentally staged secrets.env. Unstage it but keep my other staged changes.",
    setup=setup,
    check=check,
    reference=reference,
)
