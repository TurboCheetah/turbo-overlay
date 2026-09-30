# PR review autopilot

This directory holds the **non-secret code** for the PR-open automation on
`TurboCheetah/turbo-overlay`. It is separate from the Docker ebuild tester.
The deployed webhook subscription, cron job, GitHub hook, and HMAC secret live
outside Git; this PR does not change them.

## Flow

1. A signed GitHub `pull_request` webhook reaches a second route. The filter
   admits `opened`, `reopened`, and `synchronize` for this repository only and
   passes just the PR number, action, and fixed repository name to the agent.
   The original direct Telegram PR-open notification is unchanged.
2. A Hermes cron job runs immediately on accepted events and every ten minutes
   as a fallback. Its monitor emits a stable snapshot of open PRs created after
   a locally recorded PR-number baseline. Unchanged output skips the agent.
   A PR whose review-thread list exceeds the pagination safety cap is flagged
   `threads_truncated`, and the snapshot digests every fetched page so the
   unchanged-output check still fires on late-page thread changes.
   It hashes bot comment bodies rather than injecting them into the prompt.
3. The worker reviews the fresh PR and bot feedback. For authorized same-repo
   branches it may commit and push fixes. External forks get a read-only review
   and require manual merge approval. Follow the adjacent
   [autopilot skill](../../turbo-overlay-pr-autopilot/SKILL.md).
4. Before any autonomous merge, call `turbo_overlay_pr_autopilot_gate.py` with
   the full reviewed head SHA. The gate rejects unauthorized heads, changed
   commits, non-green checks, missing current-head CodeRabbit/Cubic reviews,
   standing change requests, unresolved threads, and GitHub merge conflicts.
   Cubic posts no review for a clean follow-up commit, so its successful
   check run on the exact head counts when it reports "0 issues found" and
   its last review covered an earlier commit of the PR. Empty bot thread-reply
   reviews are ignored. The `--merge` mode uses
   `gh pr merge --squash --match-head-commit` without an admin override and
   reads the PR back.

## Deployment

The scripts require Python 3.11+ and an authenticated `gh` CLI. To update the
existing default-profile installation after this change reaches the main
checkout, copy the three `turbo_overlay_pr_autopilot_*.py` scripts into
`~/.hermes/scripts/` and install the adjacent skill in the active Hermes
profile. Keep the cron job's prompt and delivery target, and keep its monitor
script set to `turbo_overlay_pr_autopilot_monitor.py`. An event-triggered
subscription should use the filter script, the `pull_request` event, and
`--cron-job <job-id>`; the GitHub webhook must have that route's HMAC secret.
Do not put the secret or `~/.hermes/data/turbo_overlay_pr_autopilot.json` in Git.

The local state file contains `{"created_after_pr": N}`. `N` is the highest
existing PR number at activation; the automation will not process older PRs
without a separate request. Set `TURBO_OVERLAY_AUTOPILOT_CONFIG` to override
its path for tests or a non-default Hermes home. A route-only test with a
synthetic signed event proves dispatch, **not** the full review/fix/merge loop;
the first genuine new PR is the end-to-end test.

## Tests

From `.agents/skills/overlay-tools`:

```bash
uv run --group dev pytest -q autopilot/tests/
uv run --group dev ruff check autopilot/
uv run --group dev ty check autopilot/
```

Never run untrusted fork code or host `emerge` as part of this automation.
