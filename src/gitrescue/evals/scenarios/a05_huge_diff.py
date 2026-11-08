from gitrescue.agent.llm import call, final
from gitrescue.evals.scenario import Check, Scenario, new_repo

ROWS = 80_000  # ~3.5 MB


def setup(root):
    ctx = new_repo(root, with_remote=False)
    rb = ctx.rb
    rb.commit("initial", {"src/__init__.py": ""})
    ctx.info["base"] = rb.sha()
    dump = "id,value,checksum\n" + "".join(f"{i},{i * 7 % 1000},{i * 2654435761 % 4294967296:08x}\n" for i in range(ROWS))
    rb.commit("add feature + data dump", {"src/feature.py": "def feature():\n    return 42\n", "data/dump.csv": dump})
    ctx.info["dump_size"] = len(dump)
    return ctx


def check(ctx, run):
    head_files = ctx.git("ls-tree", "-r", "--name-only", "HEAD").split()
    p = ctx.repo / "data" / "dump.csv"
    return Check.of(
        {
            "one commit on top of base": ctx.sha("HEAD~1") == ctx.info["base"],
            "feature kept in commit": "src/feature.py" in head_files,
            "dump removed from commit": "data/dump.csv" not in head_files,
            "dump still on disk": p.exists() and p.stat().st_size == ctx.info["dump_size"],
            "dump not staged": "data/dump.csv" not in ctx.staged(),
            "stayed within budget": run.status not in {"token_cap", "cost_cap", "step_cap"},
        }
    )


def reference(ctx):
    return [
        call("show_commit", {"include_patch": False}),
        call("git_diff", {"from_ref": "HEAD~1", "to_ref": "HEAD"}),  # exercises truncation
        call("reset", {"mode": "soft", "target": "HEAD~1"}),
        call("unstage", {"paths": ["data/dump.csv"]}),
        call("commit", {"message": "add feature"}),
        final("Removed data/dump.csv from the commit; it is untracked on disk."),
    ]


SCENARIO = Scenario(
    name="adv_huge_diff",
    kind="adversarial",
    request="My last commit accidentally included a huge generated file, data/dump.csv. Remove it from that commit but keep the rest of the commit, and leave the file on disk.",
    setup=setup,
    check=check,
    allowed_destructive=frozenset({"reset"}),
    reference=reference,
    description="A multi-megabyte diff that would blow the context window if not truncated.",
)
