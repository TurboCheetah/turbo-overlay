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
4. Before any autonomous merge, independently review the exact head and record
   the evidence, then call `turbo_overlay_pr_autopilot_gate.py` with the full
   reviewed head SHA and `--agent-reviewed`. This applies to every authorized
   same-repo PR, human-authored `TurboCheetah` and `overlay-bot[bot]` alike:
   CodeRabbit holds no review seat in this small repository and Cubic may
   decline bot-authored PRs, so the agent review may not be gated on a seat.
   Bot reviews are additional evidence; their lack of a review seat or paid
   plan must not veto a sound agent review.
   The gate still rejects unauthorized heads, changed commits, non-green checks,
   standing change requests, unresolved threads, and GitHub merge conflicts.
   Without `--agent-reviewed`, substantive current-head bot reviews are required.
   Cubic posts no review for a clean follow-up commit, so its successful
   check run on the exact head counts when it reports "0 issues found" and
   its last review covered an earlier commit of the PR. CodeRabbit likewise
   only posts a review submission when a run produces actionable comments;
   for a clean run it edits its persistent summary comment, which embeds a
   `final_review_risk_coverage` marker for the exact head, and that marker
   counts when it names the current head, the comment was updated after the
   older review, and that review covered an earlier commit of the PR. Empty
   bot thread-reply reviews are ignored. The `--merge` mode rechecks every gate,
   then uses
   `gh pr merge --squash --match-head-commit` without an admin override and
   reads the PR back.

## Review comments as overlay-bot

Confirmed findings and their fixes belong on the PR, not only in Telegram.
Reply to the bot's original review thread for a line-specific finding and
publish one Markdown PR-level summary after fixes, including impact, resolution,
reviewed SHA and actual verification. See the adjacent skill for formatting and
deduplication rules.

The separate [Post PR review as overlay-bot](../../../../.github/workflows/post-pr-review.yml)
workflow uses the same GitHub App as Check Updates, but mints a short-lived token
**inside Actions**. The App private key stays in the existing Actions secret;
the local review worker only dispatches the workflow using the owner's `gh`
authentication. This posts a formal non-approving `COMMENT` review (`kind=review`), a PR issue
comment (`kind=summary`), or a reply to an existing review comment
(`kind=reply`, `reply_to=COMMENT_ID`) as `overlay-bot[bot]`. It never approves
the PR. Its footer
names Lain as the reviewer so posting identity is not misrepresented.

Dispatch only on `master` after independently reviewing the exact PR head.
`body_b64` is base64-encoded UTF-8 Markdown (at most 24 KB decoded). The
workflow accepts only owner dispatches on the default branch, validates the
same-repository PR's authorized author, head/base and reply target, and reads
back the posted body and App login.
It updates an existing summary for the same head instead of duplicating it,
and reuses a formal review only when the body is identical — GitHub rejects
edits to a submitted `COMMENT` review (HTTP 422 in practice), so correcting a
review means posting an updated PR summary instead. It validates a reply target
even in dry-run mode. It rechecks
the PR head before mutation and after read-back; GitHub does not offer an atomic
compare-and-post for issue comments or replies, so a concurrent force-push may
still leave a stale comment, which the workflow reports as a failure.
Use `dry_run=true` on the first real dispatch to verify the App token and
target without posting; then dispatch again with `dry_run=false` to actually
post. After a real dispatch, read the run and comment ID
before reporting success. A PR for this workflow cannot prove App posting until
the workflow is merged onto the default branch.

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
