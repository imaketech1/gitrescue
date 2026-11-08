# GitRescue

An LLM agent that fixes git mistakes from plain-language requests, safely.

```bash
pip install gitrescue
export GITRESCUE_API_KEY=...            # Groq by default
gitrescue "I committed to main instead of my feature branch"
```

The agent inspects the repository, plans, runs fixes step by step through a fixed set of tools, verifies
the end state and reports what it did. By default it works on a temporary copy of your repository and
applies the result only after you approve the final plan.

## Safety model

Every tool call passes through a policy engine that the model cannot change:

- **Allowlist**: 21 tools in three risk tiers (READ, LOCAL_REVERSIBLE, DESTRUCTIVE). No shell.
- **Hard blocks**: force-pushing `main`/`master`, the remote default branch or another author's branch;
  deleting remote branches; `git clean`; config and credential changes; paths outside the repository.
  Repository hooks never run.
- **Backups**: before every change, HEAD, all branches, the stash, the index and the full working tree are
  pinned under `refs/gitrescue/backups/<id>`. `restore_backup` undoes anything.
- **Approval**: DESTRUCTIVE tools (`reset`, `rebase`, `resolve_conflict`, `push_force_with_lease`) show a
  preview and need a human yes.
- **Invariant**: after each change, no previously reachable commit or uncommitted work may be lost
  without a backup; on violation the step is rolled back automatically.
- **Untrusted content**: commit messages, file contents and branch names reach the model only as
  escaped data inside `<tool_result untrusted="true">` blocks.

## Commands

| command | |
|---|---|
| `gitrescue "<request>"` | fix something (`--no-sandbox` to work in place) |
| `gitrescue replay [run_id]` | print a recorded run (JSONL traces) |
| `gitrescue eval [--reference]` | run the 15 eval scenarios |
| `gitrescue mcp -C <repo>` | MCP server (stdio) with the same tools and policy |
| `gitrescue tools` | list tools and risk tiers |

## Configuration

`GITRESCUE_API_KEY`, `GITRESCUE_BASE_URL` (default `https://api.groq.com/openai/v1`), `GITRESCUE_MODEL`,
`GITRESCUE_MAX_STEPS` (15), `GITRESCUE_MAX_TOKENS` (200000), `GITRESCUE_MAX_COST_USD` with
`GITRESCUE_COST_PER_MTOK_IN`/`_OUT`, `GITRESCUE_PROTECTED_BRANCHES`, `GITRESCUE_TRACE_DIR`.
Any OpenAI-compatible endpoint with tool calling works.
