"""Offline checks for the short-lived overlay-bot review posting bridge."""

import base64
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "post_review.py"
_spec = importlib.util.spec_from_file_location("post_review", SCRIPT)
assert _spec is not None and _spec.loader is not None
post_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(post_review)
SHA = "a" * 40
PR = {
    "head": {"sha": SHA, "repo": {"full_name": post_review.REPO}},
    "base": {"ref": "master", "repo": {"full_name": post_review.REPO}},
    "user": {"login": "TurboCheetah"},
}


class PostingTests(unittest.TestCase):
    def test_body_validation(self):
        text = base64.b64encode(b"## Finding\n- [x] fixed").decode()
        rendered = post_review.review_body(text, SHA)
        self.assertIn("Review by Lain, posted via overlay-bot", rendered)
        # The dedup marker is the key every filter matches on; a regression
        # that drops it would silently duplicate posts.
        self.assertIn(f"<!-- lain-review:{SHA} -->", rendered)
        for bad in ("not-base64", base64.b64encode(b"\x00").decode(), ""):
            with self.assertRaises(ValueError):
                post_review.review_body(bad, SHA)
        with self.assertRaises(ValueError):
            post_review.review_body(base64.b64encode(b"a" * 24_001).decode(), SHA)
        wrapped = base64.b64encode(b"## Finding\n- [x] fixed").decode()
        self.assertIn("## Finding", post_review.review_body(wrapped[:8] + "\n" + wrapped[8:], SHA))

    def test_summary_posts_and_verifies_exact_bot_author(self):
        body = post_review.review_body(base64.b64encode(b"## Finding").decode(), SHA)
        created = {"id": 123, "body": body, "user": {"login": post_review.BOT}, "html_url": "url"}
        calls = []

        def fake_api(_token, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/pulls/7":
                return PR
            if path.startswith("/issues/7/comments?"):
                return []
            if path == "/issues/7/comments" and method == "POST":
                return created
            if path == "/issues/comments/123":
                return created
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            outcome = post_review.post("token", 7, SHA, body, "summary")
        self.assertEqual(outcome["url"], "url")
        self.assertIn(("POST", "/issues/7/comments", {"body": body}), calls)

    def test_formal_review_is_comment_not_approval(self):
        body = post_review.review_body(base64.b64encode(b"## Review").decode(), SHA)
        review = {
            "id": 789,
            "body": body,
            "user": {"login": post_review.BOT},
            "commit_id": SHA,
            "state": "COMMENTED",
            "html_url": "formal-review-url",
        }

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path.startswith("/pulls/7/reviews?"):
                return []
            if method == "POST" and path == "/pulls/7/reviews":
                self.assertEqual(payload, {"commit_id": SHA, "event": "COMMENT", "body": body})
                return review
            if path == "/pulls/7/reviews/789":
                return review
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            outcome = post_review.post("token", 7, SHA, body, "review")
        self.assertEqual(outcome["url"], "formal-review-url")

    def test_existing_formal_review_is_updated_not_duplicated(self):
        body = post_review.review_body(base64.b64encode(b"Corrected review").decode(), SHA)
        old = {
            "id": 789,
            "body": f"old <!-- lain-review:{SHA} -->",
            "user": {"login": post_review.BOT},
        }
        updated = {**old, "body": body, "commit_id": SHA, "state": "COMMENTED", "html_url": "url"}
        calls = []

        def fake_api(_token, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/pulls/7":
                return PR
            if path.startswith("/pulls/7/reviews?"):
                return [old]
            if method == "PUT" and path == "/pulls/7/reviews/789":
                self.assertEqual(payload, {"body": body})
                return updated
            if path == "/pulls/7/reviews/789":
                return updated
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            self.assertEqual(post_review.post("token", 7, SHA, body, "review")["url"], "url")
        self.assertFalse(any(method == "POST" for method, _, _ in calls))

    def test_existing_summary_is_updated_not_duplicated(self):
        body = post_review.review_body(base64.b64encode(b"new text").decode(), SHA)
        old = {
            "id": 123,
            "body": f"old <!-- lain-review:{SHA} -->",
            "user": {"login": post_review.BOT},
        }
        updated = {**old, "body": body, "html_url": "url"}

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path.startswith("/issues/7/comments?"):
                return [old]
            if method == "PATCH" and path == "/issues/comments/123":
                self.assertEqual(payload, {"body": body})
                return updated
            if path == "/issues/comments/123":
                return updated
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            self.assertEqual(post_review.post("token", 7, SHA, body, "summary")["url"], "url")

    def test_reply_must_belong_to_this_pr(self):
        with (
            patch.object(post_review, "api", side_effect=[PR, {"pull_request_url": "other"}]),
            self.assertRaisesRegex(ValueError, "target"),
        ):
            post_review.post("token", 7, SHA, "body", "reply", 123)

    def test_reply_posts_to_thread_and_verifies_bot_author(self):
        body = "## Fix\nApplied <!-- lain-review:" + SHA + " -->"
        reply = {
            "id": 456,
            "body": body,
            "in_reply_to_id": 123,
            "user": {"login": post_review.BOT},
            "html_url": "review-url",
        }

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path == "/pulls/comments/123":
                return {"pull_request_url": f"{post_review.API}/pulls/7"}
            if path.startswith("/pulls/7/comments?"):
                return []
            if method == "POST" and path == "/pulls/7/comments/123/replies":
                self.assertEqual(payload, {"body": body})
                return reply
            if path == "/pulls/comments/456":
                return reply
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            outcome = post_review.post("token", 7, SHA, body, "reply", 123)
        self.assertEqual(outcome["url"], "review-url")

    def test_existing_reply_is_reused_not_duplicated(self):
        body = "## Fix\nApplied <!-- lain-review:" + SHA + " -->"
        reply = {
            "id": 456,
            "body": body,
            "in_reply_to_id": 123,
            "user": {"login": post_review.BOT},
            "html_url": "review-url",
        }

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path == "/pulls/comments/123":
                return {"pull_request_url": f"{post_review.API}/pulls/7"}
            if path.startswith("/pulls/7/comments?"):
                return [reply]
            if path == "/pulls/comments/456":
                return reply
            raise AssertionError((method, path))

        with patch.object(post_review, "api", side_effect=fake_api):
            self.assertEqual(
                post_review.post("token", 7, SHA, body, "reply", 123)["url"], "review-url"
            )

    def test_duplicate_replies_fail_closed(self):
        body = "body"
        dup = [
            {"id": 1, "body": body, "in_reply_to_id": 123, "user": {"login": post_review.BOT}},
            {"id": 2, "body": body, "in_reply_to_id": 123, "user": {"login": post_review.BOT}},
        ]

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path == "/pulls/comments/123":
                return {"pull_request_url": f"{post_review.API}/pulls/7"}
            if path.startswith("/pulls/7/comments?"):
                return dup
            raise AssertionError((method, path))

        with (
            patch.object(post_review, "api", side_effect=fake_api),
            self.assertRaisesRegex(ValueError, "manual cleanup"),
        ):
            post_review.post("token", 7, SHA, body, "reply", 123)

    def test_reply_dry_run_validates_target_without_posting(self):
        with patch.object(
            post_review,
            "api",
            side_effect=[PR, {"pull_request_url": f"{post_review.API}/pulls/7"}],
        ) as mocked:
            result = post_review.post("token", 7, SHA, "body", "reply", 123, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(mocked.call_count, 2)

    def test_head_changed_before_mutation_fails_without_post(self):
        changed = {**PR, "head": {"sha": "b" * 40}}
        with (
            patch.object(post_review, "api", side_effect=[PR, [], changed]) as mocked,
            self.assertRaisesRegex(ValueError, "head/base"),
        ):
            post_review.post("token", 7, SHA, "body", "summary")
        self.assertEqual([call.args[1] for call in mocked.call_args_list], ["GET"] * 3)

    def test_head_changed_after_post_fails_readback(self):
        changed = {**PR, "head": {"sha": "b" * 40}}
        body = "review body"
        created = {"id": 123, "body": body, "user": {"login": post_review.BOT}, "html_url": "url"}
        with (
            patch.object(post_review, "api", side_effect=[PR, [], PR, created, created, changed]),
            self.assertRaisesRegex(ValueError, "head/base"),
        ):
            post_review.post("token", 7, SHA, body, "summary")

    def test_bad_head_and_unauthorized_base_fail_before_mutation(self):
        with (
            patch.object(post_review, "api", return_value=PR) as mocked,
            self.assertRaisesRegex(ValueError, "head/base"),
        ):
            post_review.post("token", 7, "b" * 40, "body", "summary")
        mocked.assert_called_once()
        wrong_base = {**PR, "base": {"ref": "dev", "repo": {"full_name": post_review.REPO}}}
        with (
            patch.object(post_review, "api", return_value=wrong_base),
            self.assertRaisesRegex(ValueError, "head/base"),
        ):
            post_review.post("token", 7, SHA, "body", "summary")

    def test_external_or_unauthorized_pr_fails_before_mutation(self):
        external = {**PR, "head": {"sha": SHA, "repo": {"full_name": "someone/fork"}}}
        other_author = {**PR, "user": {"login": "someone-else"}}
        for item in (external, other_author):
            with (
                patch.object(post_review, "api", return_value=item) as mocked,
                self.assertRaisesRegex(ValueError, "head/base"),
            ):
                post_review.post("token", 7, SHA, "body", "summary")
            mocked.assert_called_once_with("token", "GET", "/pulls/7")

    def test_dry_run_checks_pr_but_does_not_post(self):
        with patch.object(post_review, "api", return_value=PR) as mocked:
            outcome = post_review.post("token", 7, SHA, "body", "summary", dry_run=True)
        self.assertTrue(outcome["dry_run"])
        mocked.assert_called_once_with("token", "GET", "/pulls/7")

    def test_forged_author_fails_readback(self):
        body = "body"
        fake = {"id": 1, "body": body, "user": {"login": "TurboCheetah"}, "html_url": "url"}

        def fake_api(_token, method, path, payload=None):
            if path == "/pulls/7":
                return PR
            if path.startswith("/issues/7/comments?"):
                return []
            return fake

        with (
            patch.object(post_review, "api", side_effect=fake_api),
            self.assertRaisesRegex(ValueError, "author mismatch"),
        ):
            post_review.post("token", 7, SHA, body, "summary")

    def test_invalid_head_sha_fails_before_api(self):
        with (
            patch.object(post_review, "api") as mocked,
            self.assertRaisesRegex(ValueError, "invalid PR number"),
        ):
            post_review.post("token", 7, "g" * 40, "body", "summary")
        mocked.assert_not_called()

    def test_main_dry_run_success_path(self):
        env = {
            "GITHUB_REPOSITORY": post_review.REPO,
            "GH_TOKEN": "token",
            "HEAD_SHA": SHA,
            "PR_NUMBER": "7",
            "BODY_B64": base64.b64encode(b"## Summary").decode(),
            "REVIEW_KIND": "summary",
            "DRY_RUN": "true",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(
                post_review, "post", return_value={"dry_run": True, "kind": "summary"}
            ) as posted,
        ):
            self.assertEqual(post_review.main(), 0)
        posted.assert_called_once()
        args = posted.call_args.args
        self.assertEqual(args[1], 7)  # PR number parsed from env
        self.assertEqual(args[2], SHA)
        self.assertIn("overlay-bot", args[3])  # body rendered with footer
        self.assertEqual(args[4], "summary")
        self.assertTrue(args[6])  # dry_run honored

    def test_main_rejects_wrong_repository(self):
        with (
            patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/repo"}, clear=False),
            patch.object(post_review, "post") as posted,
        ):
            self.assertEqual(post_review.main(), 1)
        posted.assert_not_called()

    def test_main_missing_env_fails(self):
        with (
            patch.dict(os.environ, {"GITHUB_REPOSITORY": post_review.REPO}, clear=False),
            patch.object(post_review, "post") as posted,
        ):
            self.assertEqual(post_review.main(), 1)
        posted.assert_not_called()

    def test_workflow_grants_app_issues_permission(self):
        """kind=summary posts through the Issues Comments API, which requires
        the App's issues scope; a dry run cannot reveal a missing scope, so
        the workflow must ship issues:write from the start."""
        workflow = Path(__file__).resolve().parents[5] / ".github/workflows/post-pr-review.yml"
        self.assertIn("permission-issues: write", workflow.read_text())


if __name__ == "__main__":
    unittest.main()
