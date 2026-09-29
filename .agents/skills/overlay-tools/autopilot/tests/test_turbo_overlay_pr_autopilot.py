"""Unit tests for the PR autopilot's fail-closed merge decision."""

import importlib.util
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


def fixture(
    *,
    author="TurboCheetah",
    head_repo=gate.REPO,
    review_head=SHA,
    failed_check=False,
    unresolved=False,
    rabbit_comment=None,
    human_reviews=(),
    draft=False,
    state="open",
    mergeable_state="clean",
    no_repo_workflow=False,
):
    pr = {
        "state": state,
        "draft": draft,
        "head": {"sha": SHA, "repo": {"full_name": head_repo}},
        "base": {"ref": "master", "repo": {"full_name": gate.REPO}},
        "user": {"login": author},
        "mergeable": True,
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
        "reviewDecision": "",
        "statusCheckRollup": [] if no_repo_workflow else checks,
    }
    reviews = [
        {
            "user": {"login": name},
            "commit_id": review_head,
            "state": "COMMENTED",
            "body": "Reviewed",
            "submitted_at": "2026-09-28T17:00:00Z",
        }
        for name in ("coderabbitai[bot]", "cubic-dev-ai[bot]")
    ]
    reviews = [
        {"user": user, "commit_id": commit, "state": state, "body": ""}
        for user, commit, state in human_reviews
    ] + reviews
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
        if args[0] == "api" and "graphql" in args:
            return threads
        if args[0] == "api" and "reviews" in args[1]:
            return reviews
        if args[0] == "api" and "comments" in args[1]:
            return comments
        if args[0] == "api" and "pulls/" in args[1]:
            return pr
        raise AssertionError(args)

    return fake_gh


class MergeGateTests(unittest.TestCase):
    def check(self, **changes):
        with patch.object(gate, "gh", side_effect=fixture(**changes)):
            return gate.inspect(102, SHA)[0]

    def test_green_authorized_head(self):
        self.assertEqual(self.check(), [])

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
        with (
            patch.object(gate, "gh", side_effect=FileNotFoundError("gh")),
            patch.object(sys, "argv", ["gate", "102", SHA]),
        ):
            self.assertEqual(gate.main(), 1)

    def test_draft_blocks(self):
        self.assertTrue(any("closed or draft" in r for r in self.check(draft=True)))

    def test_closed_pr_blocks(self):
        self.assertTrue(any("closed or draft" in r for r in self.check(state="closed")))

    def test_non_clean_merge_state_blocks(self):
        self.assertTrue(any("merge state" in r for r in self.check(mergeable_state="dirty")))

    def test_missing_repo_workflow_blocks(self):
        self.assertTrue(
            any("no completed repository CI" in r for r in self.check(no_repo_workflow=True))
        )

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

    def test_wrong_sha_blocks(self):
        with patch.object(gate, "gh", side_effect=fixture()):
            reasons, _ = gate.inspect(102, "c" * 40)
        self.assertTrue(any("head changed" in r for r in reasons))


class WebhookFilterTests(unittest.TestCase):
    def test_only_expected_pr_events_reach_job(self):
        path = ROOT / "turbo_overlay_pr_autopilot_filter.py"
        for action, repo, number, accepted in (
            ("opened", gate.REPO, 102, True),
            ("reopened", gate.REPO, 102, True),
            ("synchronize", gate.REPO, 102, True),
            ("edited", gate.REPO, 102, False),
            ("opened", "other/repo", 102, False),
            ("opened", gate.REPO, "102", False),
            ("opened", gate.REPO, True, False),
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
            self.assertEqual(not output.get("__hermes_ignore__", False), accepted)

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
            "comments": [
                {
                    "author": {"login": "coderabbitai[bot]"},
                    "body": "secret commented body",
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

        def fake_gh(*args):
            return [pr] if args[0] == "pr" else graphql

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
                "comments": [],
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
            "comments": [],
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
        self.assertEqual(out["threads_digest"], monitor.digest("PRRT_a:0;PRRT_b:1"))

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
            "comments": [],
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
            return [pr] if args[0] == "pr" else endless

        with (
            patch.object(monitor, "CONFIG", Config()),
            patch.object(monitor, "MAX_THREAD_PAGES", 1),
            patch.object(monitor, "gh", side_effect=fake_gh),
        ):
            out = monitor.snapshot()["prs"][0]
        self.assertTrue(out["threads_truncated"])


if __name__ == "__main__":
    unittest.main()
