"""Post an exact-head PR review note as overlay-bot from trusted Actions."""

import base64
import binascii
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = "TurboCheetah/turbo-overlay"
BOT = "overlay-bot[bot]"
API = f"https://api.github.com/repos/{REPO}"


def api(token, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def comment_pages(token, path):
    """Enumerate all comments or fail closed rather than miss a duplicate."""
    for page in range(1, 11):
        rows = api(token, "GET", f"{path}?per_page=100&page={page}")
        if not isinstance(rows, list):
            raise TypeError("GitHub returned non-list comments")
        yield from rows
        if len(rows) < 100:
            return
    raise ValueError("comment pagination cap reached")


def review_body(encoded, head):
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        text = raw.decode("utf-8").strip()
    except (binascii.Error, UnicodeError) as exc:
        raise ValueError("body must be base64-encoded UTF-8") from exc
    if not text or len(raw) > 24_000 or "\x00" in text:
        raise ValueError("body must be nonempty UTF-8 Markdown (up to 24 KB)")
    return f"{text}\n\n---\n*Review by Lain, posted via overlay-bot.*\n<!-- lain-review:{head} -->"


def assert_current_head(token, number, head):
    pr = api(token, "GET", f"/pulls/{number}")
    if (
        pr["head"]["sha"] != head
        or pr["base"]["ref"] != "master"
        or pr["base"]["repo"]["full_name"] != REPO
        or (pr["head"].get("repo") or {}).get("full_name") != REPO
        or (pr.get("user") or {}).get("login") not in {"TurboCheetah", BOT}
    ):
        raise ValueError("PR head/base changed since review")


def post(token, number, head, body, kind, reply_to=0, dry_run=False):
    if number < 1 or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid PR number or reviewed head SHA")
    if kind not in {"summary", "reply", "review"} or (kind == "reply") != (reply_to > 0):
        raise ValueError("reply requires a positive target ID; other kinds must not specify one")
    assert_current_head(token, number, head)
    marker = f"<!-- lain-review:{head} -->"
    if kind == "reply":
        target = api(token, "GET", f"/pulls/comments/{reply_to}")
        if target["pull_request_url"] != f"{API}/pulls/{number}":
            raise ValueError("reply target is not on the requested PR")
    if dry_run:
        return {"dry_run": True, "number": number, "head": head, "kind": kind}
    if kind == "review":
        path = f"/pulls/{number}/reviews"
        existing = [
            r
            for r in comment_pages(token, path)
            if (r.get("user") or {}).get("login") == BOT and marker in (r.get("body") or "")
        ]
        if len(existing) > 1:
            raise ValueError("multiple bot reviews for this head; manual cleanup required")
        if existing:
            found = existing[0]
            if found["body"] == body:
                result = found
            else:
                assert_current_head(token, number, head)
                result = api(token, "PUT", f"{path}/{found['id']}", {"body": body})
        else:
            assert_current_head(token, number, head)
            result = api(token, "POST", path, {"commit_id": head, "event": "COMMENT", "body": body})
        verified = api(token, "GET", f"{path}/{result['id']}")
        if verified.get("commit_id") != head or verified.get("state") != "COMMENTED":
            raise ValueError("formal review read-back head or state mismatch")
    elif kind == "summary":
        path = f"/issues/{number}/comments"
        existing = [
            c
            for c in comment_pages(token, path)
            if (c.get("user") or {}).get("login") == BOT and marker in (c.get("body") or "")
        ]
        if len(existing) > 1:
            raise ValueError("multiple bot summaries for this head; manual cleanup required")
        if existing:
            found = existing[0]
            if found["body"] == body:
                result = found
            else:
                assert_current_head(token, number, head)
                result = api(token, "PATCH", f"/issues/comments/{found['id']}", {"body": body})
        else:
            assert_current_head(token, number, head)
            result = api(token, "POST", path, {"body": body})
        verified = api(token, "GET", f"/issues/comments/{result['id']}")
    else:
        path = f"/pulls/{number}/comments"
        existing = [
            c
            for c in comment_pages(token, path)
            if (c.get("user") or {}).get("login") == BOT
            and c.get("in_reply_to_id") == reply_to
            and c.get("body") == body
        ]
        if existing:
            result = existing[0]
        else:
            assert_current_head(token, number, head)
            result = api(token, "POST", f"{path}/{reply_to}/replies", {"body": body})
        verified = api(token, "GET", f"/pulls/comments/{result['id']}")
        if verified.get("in_reply_to_id") != reply_to:
            raise ValueError("reply read-back target mismatch")
    if verified["body"] != body or (verified.get("user") or {}).get("login") != BOT:
        raise ValueError("comment read-back body or bot author mismatch")
    assert_current_head(token, number, head)
    return {"number": number, "head": head, "kind": kind, "url": verified["html_url"]}


def main():
    try:
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise ValueError("unexpected repository")
        token = os.environ["GH_TOKEN"]
        head = os.environ["HEAD_SHA"]
        outcome = post(
            token,
            int(os.environ["PR_NUMBER"]),
            head,
            review_body(os.environ["BODY_B64"], head),
            os.environ["REVIEW_KIND"],
            int(os.environ.get("REPLY_TO", "0") or "0"),
            os.environ.get("DRY_RUN", "false").lower() == "true",
        )
    except (KeyError, TypeError, ValueError, OSError, urllib.error.HTTPError) as exc:
        print(f"Review post failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(outcome))
    return 0


if __name__ == "__main__":
    sys.exit(main())
