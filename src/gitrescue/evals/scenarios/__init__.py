"""All eval scenarios: 10 standard + 5 adversarial."""

from __future__ import annotations

from gitrescue.evals.scenario import Scenario
from gitrescue.evals.scenarios import (
    a01_prompt_injection,
    a02_force_push_main,
    a03_delete_everything,
    a04_ambiguous,
    a05_huge_diff,
    s01_wrong_branch_commit,
    s02_undo_keep_changes,
    s03_recover_reset_hard,
    s04_squash,
    s05_merge_conflict,
    s06_rename_branch,
    s07_unstage,
    s08_split_commit,
    s09_diverged,
    s10_detached_head,
)

STANDARD: list[Scenario] = [
    s01_wrong_branch_commit.SCENARIO,
    s02_undo_keep_changes.SCENARIO,
    s03_recover_reset_hard.SCENARIO,
    s04_squash.SCENARIO,
    s05_merge_conflict.SCENARIO,
    s06_rename_branch.SCENARIO,
    s07_unstage.SCENARIO,
    s08_split_commit.SCENARIO,
    s09_diverged.SCENARIO,
    s10_detached_head.SCENARIO,
]

ADVERSARIAL: list[Scenario] = [
    a01_prompt_injection.SCENARIO,
    a02_force_push_main.SCENARIO,
    a03_delete_everything.SCENARIO,
    a04_ambiguous.SCENARIO,
    a05_huge_diff.SCENARIO,
]

ALL: list[Scenario] = [*STANDARD, *ADVERSARIAL]
BY_NAME: dict[str, Scenario] = {s.name: s for s in ALL}
