from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"frontend.js": "// ui\n", "backend.py": "# api\n"})
    ctx.info["base"] = rb.sha()
    rb.commit("frontend and backend changes", {"frontend.js": "// ui\nrender();\n", "backend.py": "# api\nserve()\n"})
    ctx.info["tree"] = rb.git("rev-parse", "HEAD^{tree}").strip()
    return ctx


def _files(ctx, rev):
    return sorted(ctx.git("show", "--name-only", "--format=", rev).split())


def check(ctx, run):
    two = ctx.sha("HEAD~2") == ctx.info["base"]
    files = [_files(ctx, "HEAD~1"), _files(ctx, "HEAD")] if two else []
    return Check.of(
        {
            "two commits on top of base": two,
            "one file per commit": sorted(files) == [["backend.py"], ["frontend.js"]],
            "final content unchanged": ctx.git("rev-parse", "HEAD^{tree}").strip() == ctx.info["tree"],
            "clean tree": ctx.git("status", "--porcelain").strip() == "",
        }
    )


def reference(ctx):
    return [
        call("show_commit", {"include_patch": False}),
        call("reset", {"mode": "mixed", "target": "HEAD~1"}),
        call("stage", {"paths": ["frontend.js"]}),
        call("commit", {"message": "Update frontend"}),
        call("stage", {"paths": ["backend.py"]}),
        call("commit", {"message": "Update backend"}),
        final("Split into two commits."),
    ]


SCENARIO = Scenario(
    name="split_commit",
    kind="standard",
    request="Split my last commit into two commits: one for frontend.js and one for backend.py.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset"}),
    reference=reference,
)
