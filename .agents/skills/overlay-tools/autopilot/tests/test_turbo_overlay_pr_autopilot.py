"""Unit tests for the PR autopilot's fail-closed merge decision."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "autopilot_gate", ROOT / "turbo_overlay_pr_autopilot_gate.py"
)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
monitor_spec = importlib.util.spec_from_file_location(
    "autopilot_monitor", ROOT / "turbo_overlay_pr_autopilot_monitor.py"
)
assert monitor_spec is not None and monitor_spec.loader is not None
monitor = importlib.util.module_from_spec(monitor_spec)
monitor_spec.loader.exec_module(monitor)
SHA = "a" * 40


OLD_SHA = "e" * 40


def clean_cubic_run(**changes):
    return {
        "id": 1,
        "app": {"slug": "cubic-dev-ai"},
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "output": {"summary": "AI review completed with 1 review. 0 issues found across 1 file."},
        **changes,
    }


def fixture(
    *,
    author="TurboCheetah",
    head_repo=gate.REPO,
    base_ref="master",
    base_repo=gate.REPO,
    review_head=SHA,
    failed_check=False,
    unresolved=False,
    rabbit_comment=None,
    human_reviews=(),
    draft=False,
    state="open",
    mergeable_state="clean",
    no_repo_workflow=False,
    review_decision="",
    mergeable=True,
    transient_none_polls=0,
    head_change_after_polls=None,
    draft_after_polls=None,
    state_after_polls=None,
    review_bodies=None,
    cubic_review_head=None,
    cubic_runs=None,
    pr_commits=None,
    trailing_reviews=(),
):
    calls = {"pulls": 0}
    if cubic_runs is None:
        cubic_runs = [clean_cubic_run()]

    def pr_at_call():
        """Return the PR dict, with `mergeable` None for the first polls."""
        calls["pulls"] += 1
        merged = {**pr}
        merged["mergeable"] = None if calls["pulls"] <= transient_none_polls else mergeable
        if head_change_after_polls is not None and calls["pulls"] > head_change_after_polls:
            merged["head"] = {"sha": "b" * 40, "repo": {"full_name": head_repo}}
        if draft_after_polls is not None and calls["pulls"] > draft_after_polls:
            merged["draft"] = True
        if state_after_polls is not None and calls["pulls"] > state_after_polls:
            merged["state"] = "closed"
        return merged

    pr = {
        "state": state,
        "draft": draft,
        "head": {"sha": SHA, "repo": {"full_name": head_repo}},
        "base": {"ref": base_ref, "repo": {"full_name": base_repo}},
        "user": {"login": author},
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
    }
    checks = [
        {
            "__typename": "CheckRun",
            "name": "build",
            "workflowName": "pkgcheck",
            "status": "COMPLETED",
            "conclusion": "FAILURE" if failed_check else "SUCCESS",
        },
        {"__typename": "StatusContext", "context": "CodeRabbit", "state": "SUCCESS"},
    ]
    details = {
        "reviewDecision": review_decision,
        "statusCheckRollup": [] if no_repo_workflow else checks,
    }
    reviews = [
        {
            "user": {"login": name},
            "commit_id": (
                cubic_review_head
                if name == "cubic-dev-ai[bot]" and cubic_review_head
                else review_head
            ),
            "state": "COMMENTED",
            "body": (review_bodies or {}).get(name, "Reviewed"),
            "submitted_at": "2026-09-28T17:00:00Z",
        }
        for name in ("coderabbitai[bot]", "cubic-dev-ai[bot]")
    ]
    reviews = (
        [
            {"user": user, "commit_id": commit, "state": state, "body": ""}
            for user, commit, state in human_reviews
        ]
        + reviews
        + list(trailing_reviews)
    )
    comments = (
        [
            {
                "user": {"login": "coderabbitai[bot]"},
                "body": rabbit_comment,
                "updated_at": "2026-09-28T17:02:00Z",
            }
        ]
        if rabbit_comment is not None
        else []
    )
    threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"isResolved": not unresolved}],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            }
        }
    }

    def fake_gh(*args):
        if args[0] == "pr":
            return details
        if args[0] == "api" and args[1].endswith("/check-runs"):
            return [{"total_count": len(cubic_runs), "check_runs": cubic_runs}]
        if args[0] == "api" and args[1].endswith("/commits"):
            shas = pr_commits if pr_commits is not None else [OLD_SHA, SHA]
            return [[{"sha": sha} for sha in shas]]
        if args[0] == "api" and "graphql" in args:
            return threads
        if args[0] == "api" and "reviews" in args[1]:
            return reviews
        if args[0] == "api" and "comments" in args[1]:
            return comments
        if args[0] == "api" and "pulls/" in args[1]:
            return pr_at_call()
        raise AssertionError(args)

    return fake_gh


class MergeGateTests(unittest.TestCase):
    def check(self, **changes):
        with patch.object(gate, "gh", side_effect=fixture(**changes)):
            return gate.inspect(102, SHA)[0]

    def test_green_authorized_head(self):
        self.assertEqual(self.check(), [])

    def test_clean_cubic_check_covers_followup_commit(self):
        self.assertEqual(self.check(cubic_review_head=OLD_SHA), [])

    def test_cubic_check_with_issues_does_not_cover_followup(self):
        summary = {"summary": "AI review completed with 1 review. 1 issue found across 2 files."}
        runs = [clean_cubic_run(output=summary)]
        reasons = self.check(cubic_review_head=OLD_SHA, cubic_runs=runs)
        self.assertIn("cubic-dev-ai has not completed a substantive current-head review", reasons)

    def test_cubic_fallback_rejects_bad_runs(self):
        cases = {
            "missing": [],
            "in progress": [clean_cubic_run(status="in_progress", conclusion=None)],
            "failed": [clean_cubic_run(conclusion="failure")],
            "other head": [clean_cubic_run(head_sha="b" * 40)],
            "other app": [clean_cubic_run(app={"slug": "impostor"})],
            # Only the newest run counts; an older clean run is superseded.
            "superseded": [
                clean_cubic_run(id=1),
                clean_cubic_run(id=2, output={"summary": "2 issues found"}),
            ],
        }
        for label, runs in cases.items():
            reasons = self.check(cubic_review_head=OLD_SHA, cubic_runs=runs)
            self.assertTrue(any("cubic-dev-ai" in r for r in reasons), label)

    def test_cubic_fallback_requires_review_of_this_pr(self):
        reasons = self.check(cubic_review_head=OLD_SHA, pr_commits=[SHA])
        self.assertTrue(any("cubic-dev-ai" in r for r in reasons))

    def test_clean_check_does_not_cover_stale_coderabbit_review(self):
        reasons = self.check(review_head=OLD_SHA)
        self.assertIn("coderabbitai has not completed a substantive current-head review", reasons)

    def test_green_path_returns_freshest_head(self):
        # The merge path passes inspect's head to --match-head-commit and
        # judges bot-review freshness against it; a regression returning a
        # stale first-read head must not pass.
        with (
            patch.object(gate, "gh", side_effect=fixture(transient_none_polls=1)),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons, head = gate.inspect(102, SHA)
        self.assertEqual(reasons, [])
        self.assertEqual(head, SHA)

    def test_unexpected_target_branch_blocks(self):
        reasons = self.check(base_ref="dev")
        self.assertTrue(any("target repository or branch" in r for r in reasons))

    def test_unexpected_target_repo_blocks(self):
        reasons = self.check(base_repo="outside/turbo-overlay")
        self.assertTrue(any("target repository or branch" in r for r in reasons))

    def test_external_fork_never_merges(self):
        reasons = self.check(head_repo="outside/turbo-overlay")
        self.assertTrue(any("manual approval" in r for r in reasons))

    def test_unauthorized_author_never_merges(self):
        reasons = self.check(author="outside")
        self.assertTrue(any("manual approval" in r for r in reasons))

    def test_stale_bot_review_blocks(self):
        self.assertTrue(any("current-head review" in r for r in self.check(review_head="b" * 40)))

    def test_failed_check_blocks(self):
        self.assertTrue(any("check not successful" in r for r in self.check(failed_check=True)))

    def test_unresolved_thread_blocks(self):
        self.assertTrue(any("unresolved" in r for r in self.check(unresolved=True)))

    def test_rate_limit_marker_after_review_blocks(self):
        body = "<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->"
        self.assertTrue(any("rate-limit" in r for r in self.check(rabbit_comment=body)))

    def test_stale_skip_block_does_not_block_current_head_review(self):
        # The edited summary comment keeps its skip block after a manual full review.
        body = (
            "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
            "Review skipped. This code discusses a rate limit and skip review.\n"
            "<!-- end of auto-generated comment: skip review by coderabbit.ai -->"
        )
        self.assertEqual(self.check(rabbit_comment=body), [])

    def test_review_body_mentioning_rate_limit_does_not_block(self):
        # A review body that explains the rate-limit check must not be mistaken
        # for a rate-limited review: only the CodeRabbit marker comment counts.
        bodies = {"coderabbitai[bot]": "Reviewed. This PR changes the rate limit check."}
        self.assertEqual(self.check(review_bodies=bodies), [])

    def test_review_body_marker_blocks(self):
        body = "<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->"
        reasons = self.check(review_bodies={"coderabbitai[bot]": body})
        self.assertTrue(any("rate-limit" in r for r in reasons))

    def test_bot_thread_reply_does_not_displace_real_review(self):
        # GitHub stores a thread reply as a COMMENTED review with an empty body.
        reply = {
            "user": {"login": "coderabbitai[bot]"},
            "commit_id": SHA,
            "state": "COMMENTED",
            "body": "",
            "submitted_at": "2026-09-28T18:00:00Z",
        }
        self.assertEqual(self.check(trailing_reviews=[reply]), [])

    def test_empty_comment_review_blocks(self):
        # A COMMENTED review with no visible content is not a substantive
        # current-head review even when it is anchored at the head SHA.
        reasons = self.check(review_bodies={"coderabbitai[bot]": "", "cubic-dev-ai[bot]": ""})
        self.assertTrue(any("current-head review" in r for r in reasons))

    def test_marker_only_comment_review_blocks(self):
        # HTML marker comments alone do not make a review substantive.
        body = "<!-- cubic:review-post:abc -->"
        reasons = self.check(review_bodies={"coderabbitai[bot]": body})
        self.assertTrue(any("current-head review" in r for r in reasons))

    def test_marker_plus_text_review_passes(self):
        # Marker comments with real findings text remain substantive.
        body = "<!-- cubic:review-post:abc -->\n**1 issue found**"
        self.assertEqual(self.check(review_bodies={"coderabbitai[bot]": body}), [])

    def test_stale_human_changes_request_blocks(self):
        reviews = [({"login": "TurboCheetah"}, "b" * 40, "CHANGES_REQUESTED")]
        reasons = self.check(human_reviews=reviews)
        self.assertTrue(any("changes requested by TurboCheetah" in r for r in reasons))

    def test_later_comment_does_not_clear_changes_request(self):
        reviews = [
            ({"login": "TurboCheetah"}, "b" * 40, "CHANGES_REQUESTED"),
            ({"login": "TurboCheetah"}, SHA, "COMMENTED"),
        ]
        self.assertTrue(any("changes requested" in r for r in self.check(human_reviews=reviews)))

    def test_approval_or_dismissal_clears_changes_request(self):
        for later in ("APPROVED", "DISMISSED"):
            reviews = [
                ({"login": "TurboCheetah"}, "b" * 40, "CHANGES_REQUESTED"),
                ({"login": "TurboCheetah"}, SHA, later),
            ]
            self.assertEqual(self.check(human_reviews=reviews), [], later)

    def test_deleted_reviewer_does_not_crash(self):
        self.assertEqual(self.check(human_reviews=[(None, SHA, "COMMENTED")]), [])

    def test_missing_gh_reports_blocked_without_traceback(self):
        stderr = io.StringIO()
        with (
            patch.object(gate, "gh", side_effect=FileNotFoundError("gh")),
            patch.object(sys, "argv", ["gate", "102", SHA]),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(gate.main(), 1)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_draft_blocks(self):
        self.assertTrue(any("closed or draft" in r for r in self.check(draft=True)))

    def test_closed_pr_blocks(self):
        self.assertTrue(any("closed or draft" in r for r in self.check(state="closed")))

    def test_non_clean_merge_state_blocks(self):
        self.assertTrue(any("merge state" in r for r in self.check(mergeable_state="dirty")))

    def test_transient_mergeable_none_repolls_and_passes(self):
        # GitHub computes mergeable asynchronously; a null on the first read
        # must re-poll instead of failing closed.
        with (
            patch.object(gate, "gh", side_effect=fixture(transient_none_polls=1)),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons = gate.inspect(102, SHA)[0]
        self.assertEqual(reasons, [])

    def test_persistent_mergeable_none_blocks(self):
        # Still fail closed when the mergeable field never resolves.
        with (
            patch.object(gate, "gh", side_effect=fixture(transient_none_polls=99)),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons = gate.inspect(102, SHA)[0]
        self.assertTrue(any("merge state" in r for r in reasons))

    def test_head_change_during_mergeable_poll_blocks(self):
        # The re-poll exists because mergeable can be null right after a push;
        # that same push can change the head. A head observed on any poll must
        # be compared against the expected head, not just the first response.
        with (
            patch.object(
                gate, "gh", side_effect=fixture(transient_none_polls=1, head_change_after_polls=1)
            ),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons = gate.inspect(102, SHA)[0]
        self.assertTrue(any("head changed" in r for r in reasons))

    def test_draft_during_mergeable_poll_blocks(self):
        # The state/draft gates must apply to the freshest polled response:
        # a PR made draft between the first read and the final poll must not
        # report merge-ready.
        with (
            patch.object(
                gate, "gh", side_effect=fixture(transient_none_polls=1, draft_after_polls=1)
            ),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons = gate.inspect(102, SHA)[0]
        self.assertTrue(any("closed or draft" in r for r in reasons))

    def test_closed_during_mergeable_poll_blocks(self):
        # Same freshness requirement for a PR closed mid-poll.
        with (
            patch.object(
                gate, "gh", side_effect=fixture(transient_none_polls=1, state_after_polls=1)
            ),
            patch.object(gate.time, "sleep", return_value=None),
        ):
            reasons = gate.inspect(102, SHA)[0]
        self.assertTrue(any("closed or draft" in r for r in reasons))

    def test_missing_repo_workflow_blocks(self):
        self.assertTrue(
            any("no completed repository CI" in r for r in self.check(no_repo_workflow=True))
        )

    def test_review_decision_gate_blocks(self):
        for decision in ("CHANGES_REQUESTED", "REVIEW_REQUIRED"):
            reasons = self.check(review_decision=decision)
            self.assertTrue(any("GitHub review gate" in r for r in reasons), decision)

    def test_merge_uses_reviewed_sha_and_verifies_readback(self):
        merged = {
            "merged": True,
            "head": {"sha": SHA},
            "html_url": "https://github.com/TurboCheetah/turbo-overlay/pull/102",
        }
        with (
            patch.object(gate, "inspect", return_value=([], SHA)),
            patch.object(gate, "gh", return_value=merged),
            patch.object(gate.subprocess, "run") as command,
            patch.object(sys, "argv", ["gate", "102", SHA, "--merge"]),
        ):
            self.assertEqual(gate.main(), 0)
        command.assert_called_once_with(
            ["gh", "pr", "merge", "102", "-R", gate.REPO, "--squash", "--match-head-commit", SHA],
            check=True,
            timeout=90,
        )

    def test_blocked_pr_never_calls_merge(self):
        with (
            patch.object(gate, "inspect", return_value=(["not an authorized author"], SHA)),
            patch.object(gate.subprocess, "run") as command,
            patch.object(sys, "argv", ["gate", "102", SHA, "--merge"]),
        ):
            self.assertEqual(gate.main(), 1)
        command.assert_not_called()

    def test_readback_mismatch_does_not_report_merged(self):
        merged = {"merged": False, "head": {"sha": "d" * 40}, "html_url": ""}
        with (
            patch.object(gate, "inspect", return_value=([], SHA)),
            patch.object(gate, "gh", return_value=merged),
            patch.object(gate.subprocess, "run") as command,
            patch.object(sys, "argv", ["gate", "102", SHA, "--merge"]),
        ):
            self.assertEqual(gate.main(), 1)
        command.assert_called_once()
        output = str(command.call_args)
        self.assertIn("--squash", output)

    def test_merge_rechecks_inspect_before_merging(self):
        # --match-head-commit pins only the SHA; a same-head review change
        # between the first inspect and the merge command must not slip
        # through, so inspect runs again immediately before the merge.
        merged = {
            "merged": True,
            "head": {"sha": SHA},
            "html_url": "https://github.com/TurboCheetah/turbo-overlay/pull/102",
        }
        with (
            patch.object(gate, "inspect", return_value=([], SHA)) as inspected,
            patch.object(gate, "gh", return_value=merged),
            patch.object(gate.subprocess, "run") as command,
            patch.object(sys, "argv", ["gate", "102", SHA, "--merge"]),
        ):
            self.assertEqual(gate.main(), 0)
        self.assertEqual(inspected.call_count, 2)
        command.assert_called_once()

    def test_merge_recheck_blocking_reason_aborts_merge(self):
        # A reviewer posting a same-head blocker between the two inspects
        # must abort the merge even though the first inspect was clean.
        with (
            patch.object(
                gate,
                "inspect",
                side_effect=[
                    ([], SHA),
                    (["coderabbitai has not completed a substantive current-head review"], SHA),
                ],
            ),
            patch.object(gate.subprocess, "run") as command,
            patch.object(sys, "argv", ["gate", "102", SHA, "--merge"]),
        ):
            self.assertEqual(gate.main(), 1)
        command.assert_not_called()

    def test_gh_failure_includes_stderr(self):
        # str(CalledProcessError) drops the diagnostic stderr; the re-raise
        # must carry it so an unattended gate failure is explainable.
        error = subprocess.CalledProcessError(
            1, ["gh", "api"], output="", stderr="rate limit exceeded"
        )
        with (
            patch.object(gate.subprocess, "run", side_effect=error),
            self.assertRaises(subprocess.SubprocessError) as caught,
        ):
            gate.gh("api", "repos/TurboCheetah/turbo-overlay/pulls/102")
        self.assertIn("rate limit exceeded", str(caught.exception))

    def test_wrong_sha_blocks(self):
        with patch.object(gate, "gh", side_effect=fixture()):
            reasons, _ = gate.inspect(102, "c" * 40)
        self.assertTrue(any("head changed" in r for r in reasons))


class WebhookFilterTests(unittest.TestCase):
    def test_only_expected_pr_events_reach_job(self):
        path = ROOT / "turbo_overlay_pr_autopilot_filter.py"
        for action, repo, number, expected in (
            ("opened", gate.REPO, 102, {"repo": gate.REPO, "number": 102, "action": "opened"}),
            ("reopened", gate.REPO, 102, {"repo": gate.REPO, "number": 102, "action": "reopened"}),
            (
                "synchronize",
                gate.REPO,
                102,
                {"repo": gate.REPO, "number": 102, "action": "synchronize"},
            ),
            ("edited", gate.REPO, 102, None),
            ("opened", "other/repo", 102, None),
            ("opened", gate.REPO, "102", None),
            ("opened", gate.REPO, True, None),
            ("opened", gate.REPO, 0, None),
            ("opened", gate.REPO, -1, None),
        ):
            event = {
                "action": action,
                "repository": {"full_name": repo},
                "pull_request": {"number": number},
            }
            result = subprocess.run(
                [sys.executable, str(path)],
                input=json.dumps(event),
                capture_output=True,
                text=True,
                check=True,
            )
            output = json.loads(result.stdout)
            if expected is None:
                self.assertTrue(output.get("__hermes_ignore__", False), (action, repo, number))
            else:
                self.assertEqual(output, expected, (action, repo, number))

    def test_malformed_payload_fails_closed(self):
        path = ROOT / "turbo_overlay_pr_autopilot_filter.py"
        for bad in ("", "not json", "[]", '"x"', "null"):
            result = subprocess.run(
                [sys.executable, str(path)],
                input=bad,
                capture_output=True,
                text=True,
                check=True,
            )
            output = json.loads(result.stdout)
            self.assertTrue(output.get("__hermes_ignore__", False), bad)

    def test_non_string_action_fails_closed(self):
        path = ROOT / "turbo_overlay_pr_autopilot_filter.py"
        for action in (["edited"], {"nested": True}, 7):
            event = {
                "action": action,
                "repository": {"full_name": gate.REPO},
                "pull_request": {"number": 102},
            }
            result = subprocess.run(
                [sys.executable, str(path)],
                input=json.dumps(event),
                capture_output=True,
                text=True,
                check=True,
            )
            output = json.loads(result.stdout)
            self.assertTrue(output.get("__hermes_ignore__", False), action)


class MonitorTests(unittest.TestCase):
    def test_new_pr_snapshot_is_stable_and_does_not_include_raw_bot_text(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "mergeable": "UNSTABLE",
            "reviewDecision": "REVIEW_REQUIRED",
            "headRepositoryOwner": {"login": "TurboCheetah"},
            "author": {"login": "overlay-bot[bot]"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [
                {
                    "author": {"login": "cubic-dev-ai"},
                    "state": "COMMENTED",
                    "submittedAt": "2026-09-28T17:00:00Z",
                    "body": "private feedback",
                }
            ],
        }
        graphql = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "PRRT_1", "isResolved": False}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        }

        rest_comments = [
            [
                {"user": {"login": "coderabbitai[bot]"}, "body": "secret commented body"},
                {"user": {"login": "someone"}, "body": "human comment"},
            ]
        ]

        def fake_gh(*args):
            if args[0] == "pr":
                return [pr]
            return rest_comments if args[1].endswith("/comments") else graphql

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            first = monitor.snapshot()
            self.assertEqual(first, monitor.snapshot())
        self.assertEqual(first["prs"][0]["unresolved_threads"], 1)
        self.assertEqual(first["prs"][0]["mergeable"], "UNSTABLE")
        self.assertEqual(first["prs"][0]["review_decision"], "REVIEW_REQUIRED")
        self.assertNotIn("private feedback", json.dumps(first))
        self.assertNotIn("secret commented body", json.dumps(first))
        self.assertEqual(
            first["prs"][0]["bot_comments"][0]["body_hash"], monitor.digest("secret commented body")
        )

    def test_oversized_pr_is_flagged_without_failing_other_prs(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        def pr(number):
            return {
                "number": number,
                "headRefOid": SHA,
                "baseRefName": "master",
                "author": {"login": "TurboCheetah"},
                "isDraft": False,
                "statusCheckRollup": [],
                "latestReviews": [],
            }

        def threads(has_next):
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {
                                    "hasNextPage": has_next,
                                    "endCursor": "c1" if has_next else None,
                                },
                            }
                        }
                    }
                }
            }

        def fake_gh(*args):
            if args[1].endswith("/comments"):
                return [[]]
            if args[0] == "pr":
                return [pr(102), pr(103)]
            # PR 102's thread list never ends (hits the cap); PR 103 has one page.
            return threads("number=102" in args)

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "MAX_THREAD_PAGES", 2),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            prs = monitor.snapshot()["prs"]
        self.assertEqual(
            [(p["number"], p["threads_truncated"]) for p in prs], [(102, True), (103, False)]
        )

    def test_thread_pagination_accumulates_all_pages(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }
        first_page = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "PRRT_a", "isResolved": False}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        }
                    }
                }
            }
        }
        second_page = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "PRRT_b", "isResolved": True}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }

        def fake_gh(*args):
            if args[1].endswith("/comments"):
                return [[]]
            if args[0] == "pr":
                return [pr]
            return second_page if "cursor=c1" in args else first_page

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            out = monitor.snapshot()["prs"][0]
        # Digest must reflect both pages; unresolved count covers both too.
        self.assertFalse(out["threads_truncated"])
        self.assertEqual(out["unresolved_threads"], 1)
        expected = monitor.digest(
            ";".join(
                sorted(
                    [
                        monitor.thread_state_key({"id": "PRRT_a", "isResolved": False}),
                        monitor.thread_state_key({"id": "PRRT_b", "isResolved": True}),
                    ]
                )
            )
        )
        self.assertEqual(out["threads_digest"], expected)

    def test_thread_page_cap_flags_truncation(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }
        endless = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": "PRRT_a", "isResolved": True}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        }
                    }
                }
            }
        }

        def fake_gh(*args):
            if args[1].endswith("/comments"):
                return [[]]
            return [pr] if args[0] == "pr" else endless

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "MAX_THREAD_PAGES", 1),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            out = monitor.snapshot()["prs"][0]
        self.assertTrue(out["threads_truncated"])

    def test_thread_fetch_exceeding_deadline_fails_loudly(self):
        # A pathological PR must not monopolize a sweep: an expired budget
        # bails before any further gh call so the cron fallback cannot
        # overlap an hours-long run.
        with patch.object(monitor, "gh") as fetched, self.assertRaises(ValueError):
            monitor.fetch_threads(102, deadline=-1.0)
        fetched.assert_not_called()
        with patch.object(monitor, "gh") as fetched, self.assertRaises(ValueError):
            monitor.fetch_thread_comments({"id": "PRRT_x"}, deadline=-1.0)
        fetched.assert_not_called()

    def test_deadline_exhaustion_is_a_per_pr_error_not_a_blank_sweep(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }

        def fake_gh(*args):
            if args[0] == "pr":
                return [pr]
            if args[1].endswith("/comments"):
                return [[]]
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "SWEEP_DEADLINE_SECONDS", -1),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            out = monitor.snapshot()
        self.assertIn("error", out["prs"][0])
        self.assertIn("sweep deadline", out["prs"][0]["error"])
        self.assertEqual(out["prs"][0]["number"], 102)

    def test_bot_comments_span_every_page_and_ignore_order(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }
        threads = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }
        human = [{"user": {"login": "someone"}, "body": "hi"}] * 100
        late_bot = {"user": {"login": "cubic-dev-ai[bot]"}, "body": "comment 101"}
        early_bot = {"user": {"login": "coderabbitai[bot]"}, "body": "summary"}

        def run(pages):
            def fake_gh(*args):
                if args[0] == "pr":
                    return [pr]
                return pages if args[1].endswith("/comments") else threads

            with (
                patch.object(monitor, "CONFIG", Config()),
                patch.object(monitor, "gh", side_effect=fake_gh),
            ):
                return monitor.snapshot()["prs"][0]["bot_comments"]

        comments = run([[early_bot, *human[:99]], [human[0], late_bot]])
        self.assertIn(
            {"bot": "cubic-dev-ai[bot]", "body_hash": monitor.digest("comment 101")}, comments
        )
        self.assertEqual(comments, run([[late_bot], [early_bot]]))

    def test_thread_comment_edit_changes_threads_digest(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }

        def threads(comment_body):
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "PRRT_t",
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [{"databaseId": 1, "body": comment_body}],
                                            "pageInfo": {"hasNextPage": False},
                                        },
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        calls = {"comment_edited": False}

        def fake_gh(*args):
            if args[1].endswith("/comments"):
                return [[]]
            if args[0] == "pr":
                return [pr]
            body = "first wording" if not calls["comment_edited"] else "edited wording"
            calls["comment_edited"] = not calls["comment_edited"]
            return threads(body)

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            first = monitor.snapshot()["prs"][0]["threads_digest"]
            second = monitor.snapshot()["prs"][0]["threads_digest"]
        self.assertNotEqual(first, second)

    def test_second_comment_page_change_changes_threads_digest(self):
        # A thread can exceed the 100-comment page the thread-list query
        # fetches; an edit on a later comment page must still be reflected
        # in the fingerprint so the sweep reconciles it.
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        pr = {
            "number": 102,
            "headRefOid": SHA,
            "baseRefName": "master",
            "author": {"login": "TurboCheetah"},
            "isDraft": False,
            "statusCheckRollup": [],
            "latestReviews": [],
        }

        def threads(first_page_body):
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "PRRT_x",
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [{"databaseId": 1, "body": first_page_body}],
                                            "pageInfo": {"hasNextPage": True, "endCursor": "cc1"},
                                        },
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        def second_page(second_page_body):
            return {
                "data": {
                    "node": {
                        "comments": {
                            "nodes": [{"databaseId": 2, "body": second_page_body}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        calls = {"second_body": "second page one", "first_body": "first page one"}

        def fake_gh(*args):
            if args[1].endswith("/comments"):
                return [[]]
            if args[0] == "pr":
                return [pr]
            # The per-thread comment pagination call targets the node id;
            # the thread-list query carries the PR number.
            if "id=PRRT_x" in args:
                return second_page(calls["second_body"])
            return threads(calls["first_body"])

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            first = monitor.snapshot()["prs"][0]["threads_digest"]
            calls["second_body"] = "second page edited"
            second = monitor.snapshot()["prs"][0]["threads_digest"]
        self.assertNotEqual(first, second)

    def test_per_pr_fetch_failure_does_not_blank_snapshot(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        def pr(number):
            return {
                "number": number,
                "headRefOid": SHA,
                "baseRefName": "master",
                "author": {"login": "TurboCheetah"},
                "isDraft": False,
                "statusCheckRollup": [],
                "latestReviews": [],
            }

        def fake_gh(*args):
            if args[0] == "pr":
                return [pr(102), pr(103)]
            if args[1].endswith("/comments"):
                return [[]]
            if "number=103" in args:
                raise subprocess.SubprocessError("throttled")
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            prs = monitor.snapshot()["prs"]
        by_number = {p["number"]: p for p in prs}
        self.assertIn(102, by_number)
        self.assertIn("error", by_number[103])
        self.assertTrue(by_number[103]["error"].startswith("per-PR fetch failed:"))

    def test_pr_list_beyond_one_page_single_request(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        def pr(number):
            return {
                "number": number,
                "headRefOid": SHA,
                "baseRefName": "master",
                "author": {"login": "TurboCheetah"},
                "isDraft": False,
                "statusCheckRollup": [],
                "latestReviews": [],
            }

        calls = {"list": 0}

        def fake_gh(*args):
            if args[0] == "pr":
                calls["list"] += 1
                # A single `--limit` request returns more than one API page
                # (gh paginates internally). GitHub lists open PRs
                # newest-first, so the response holds the highest numbers
                # followed by older, lower-numbered PRs below the cutoff.
                return [pr(102 - i) for i in range(150)]
            if args[1].endswith("/comments"):
                return [[]]
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            prs = monitor.snapshot()["prs"]
        # One call with the internal-pagination limit; the cutoff (101)
        # keeps only PR 102 from a response spanning many API pages.
        self.assertEqual(calls["list"], 1)
        self.assertEqual([p["number"] for p in prs], [102])

    def test_pr_list_safety_cap_raises(self):
        class Config:
            def read_text(self):
                return '{"created_after_pr":101}'

        def pr(number):
            return {
                "number": number,
                "headRefOid": SHA,
                "baseRefName": "master",
                "author": {"login": "TurboCheetah"},
                "isDraft": False,
                "statusCheckRollup": [],
                "latestReviews": [],
            }

        def fake_gh(*args):
            if args[0] == "pr":
                # A repo with more open PRs than the cap would be truncated
                # silently by the API; the monitor must fail loudly instead.
                return [pr(10000 - i) for i in range(monitor.MAX_PR_PAGES * 100)]
            raise AssertionError(args)

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "gh", side_effect=fake_gh),
            self.assertRaisesRegex(ValueError, "safety cap"),
        ):
            monitor.snapshot()


if __name__ == "__main__":
    unittest.main()
