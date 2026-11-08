from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo
from gitrescue.git import run_git


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"config.py": "TIMEOUT = 10\n"})
    rb.branch("cache")
    rb.commit("add retries", {"config.py": "TIMEOUT = 10\nRETRIES = 3\n"})
    rb.checkout("cache")
    rb.commit("add cache flag", {"config.py": "TIMEOUT = 10\nCACHE = True\n"})
    rb.checkout("main")
    ctx.info["main"] = rb.sha("main")
    ctx.info["cache"] = rb.sha("cache")
    run_git(ctx.repo, "merge", "cache", "-m", "Merge branch 'cache'", _guard=False)  # conflicts
    return ctx


def check(ctx, run):
    parents = ctx.git("rev-list", "--parents", "-n1", "main").split()[1:]
    content = ctx.tree_file("main", "config.py") or ""
    return Check.of(
        {
            "merge commit with both parents": sorted(parents) == sorted([ctx.info["main"], ctx.info["cache"]]),
            "keeps RETRIES": "RETRIES = 3" in content,
            "keeps CACHE": "CACHE = True" in content,
            "keeps TIMEOUT": "TIMEOUT = 10" in content,
            "no conflict markers": "<<<<<<<" not in content and ">>>>>>>" not in content,
            "merge finished": not ctx.in_progress(),
        }
    )


def reference(ctx):
    return [
        call("git_status"),
        call("git_diff", {"paths": ["config.py"]}),
        call("resolve_conflict", {"file": "config.py", "content": "TIMEOUT = 10\nRETRIES = 3\nCACHE = True\n"}),
        call("commit", {"message": "Merge branch 'cache'"}),
        final("Resolved and merged."),
    ]


SCENARIO = Scenario(
    name="simple_merge_conflict",
    kind="standard",
    request="I'm in the middle of merging 'cache' into main and config.py has a conflict. Keep both settings and finish the merge.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"resolve_conflict"}),
    reference=reference,
)
