#!/usr/bin/env python3
"""Fail-closed merge gate for automated turbo-overlay PR reviews."""

import argparse
import json
import re
import subprocess
import time

REPO = "TurboCheetah/turbo-overlay"
ALLOWED = {"TurboCheetah", "overlay-bot[bot]"}
BOTS = {"coderabbitai", "cubic-dev-ai"}
# CodeRabbit wraps its rate-limit notice in an HTML marker comment. Match the
# marker, not free text: its edited summary comment can quote code or old notes.
RABBIT_RATE_LIMIT = re.compile(r"<!--[^>]*rate limit[^>]*coderabbit\.ai[^>]*-->", re.IGNORECASE)


def _visible_body(body):
    """Strip HTML marker comments so a marker-only review is not substantive."""
    return re.sub(r"<!--.*?-->", "", body or "", flags=re.DOTALL).strip()


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=90)
    return json.loads(result.stdout)


def flatten_pages(items):
    """gh api --paginate --slurp returns a list of page arrays; merge them."""
    if all(isinstance(i, list) for i in items):
        return [item for page in items for item in page]
    return items


def inspect(number, expected_head):
    pr = gh("api", f"repos/{REPO}/pulls/{number}")
    reasons = []
    if pr["state"] != "open" or pr.get("draft"):
        reasons.append("PR is closed or draft")
    if pr["base"]["ref"] != "master" or pr["base"]["repo"]["full_name"] != REPO:
        reasons.append("unexpected target repository or branch")
    if pr["user"]["login"] not in ALLOWED or (pr["head"]["repo"] or {}).get("full_name") != REPO:
        reasons.append("not an authorized same-repository author/head; manual approval required")
    # GitHub computes `mergeable` asynchronously and returns null until it
    # finishes; re-poll briefly so a first-read null does not false-block.
    attempts = 0
    while pr.get("mergeable") is None and attempts < 5:
        time.sleep(2)
        pr = gh("api", f"repos/{REPO}/pulls/{number}")
        attempts += 1
    # A push can land while re-polling, so the head that review checks and
    # --match-head-commit validate must come from the freshest response, not
    # the first one read before the poll.
    head = pr["head"]["sha"]
    if head != expected_head:
        reasons.append("head changed since review; review new commit")
    if pr.get("mergeable") is not True or pr.get("mergeable_state") != "clean":
        reasons.append(f"GitHub merge state not clean: {pr.get('mergeable_state')}")

    details = gh(
        "pr", "view", str(number), "-R", REPO, "--json", "statusCheckRollup,reviewDecision"
    )
    checks = details["statusCheckRollup"]
    if not checks or not any(c.get("workflowName") in {"ci", "pkgcheck"} for c in checks):
        reasons.append("no completed repository CI or pkgcheck workflow")
    for c in checks:
        name = c.get("name") or c.get("context") or "unknown"
        if c.get("__typename") == "StatusContext":
            good = c.get("state") == "SUCCESS"
        else:
            good = c.get("status") == "COMPLETED" and c.get("conclusion") == "SUCCESS"
        if not good:
            reasons.append(f"check not successful: {name}")
    if details.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        reasons.append(f"GitHub review gate: {details['reviewDecision']}")

    reviews = flatten_pages(
        gh("api", f"repos/{REPO}/pulls/{number}/reviews", "--paginate", "--slurp")
    )
    latest = {}
    verdicts = {}
    for review in reviews:
        login = (review.get("user") or {}).get("login") or "ghost"
        user = login.lower().removesuffix("[bot]")
        if user in BOTS:
            latest[user] = review
        # GitHub keeps a request for changes in force across later commits until
        # the reviewer approves or it is dismissed; comments do not clear it.
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            verdicts[login] = review["state"]
    for login, state in sorted(verdicts.items()):
        if state == "CHANGES_REQUESTED":
            reasons.append(f"changes requested by {login} and not approved or dismissed")
    for bot in BOTS:
        review = latest.get(bot)
        if (
            not review
            or review["commit_id"] != head
            or review["state"] not in {"APPROVED", "COMMENTED"}
        ):
            reasons.append(f"{bot} has not completed a substantive current-head review")
        elif RABBIT_RATE_LIMIT.search(review.get("body") or ""):
            # CodeRabbit embeds its rate-limit notice in an HTML marker comment.
            # Match only that marker: a review body explaining or quoting the
            # rate-limit check must not be mistaken for a rate-limited review.
            reasons.append(f"{bot} review was rate-limited")
        elif review["state"] == "COMMENTED" and not _visible_body(review.get("body")):
            # A COMMENTED review with no visible content is not a substantive
            # review of this head; require actual findings or text.
            reasons.append(f"{bot} has not completed a substantive current-head review")

    comments = flatten_pages(
        gh("api", f"repos/{REPO}/issues/{number}/comments", "--paginate", "--slurp")
    )
    rabbit = latest.get("coderabbitai")
    for comment in comments:
        user = ((comment.get("user") or {}).get("login") or "").lower().removesuffix("[bot]")
        if (
            user == "coderabbitai"
            and rabbit
            and comment["updated_at"] >= rabbit["submitted_at"]
            and RABBIT_RATE_LIMIT.search(comment.get("body") or "")
        ):
            reasons.append("CodeRabbit posted a rate-limit notice after its latest review")
            break

    threads = gh(
        "api",
        "graphql",
        "-f",
        f"owner={REPO.split('/')[0]}",
        "-f",
        f"name={REPO.split('/')[1]}",
        "-F",
        f"number={number}",
        "-f",
        "query=query($owner:String!, $name:String!, $number:Int!) { "
        "repository(owner:$owner, name:$name) { pullRequest(number:$number) { "
        "reviewThreads(first:100) { nodes { isResolved } "
        "pageInfo { hasNextPage } } } } }",
    )
    data = threads["data"]["repository"]["pullRequest"]["reviewThreads"]
    if data["pageInfo"]["hasNextPage"] or any(not t["isResolved"] for t in data["nodes"]):
        reasons.append("unresolved or unenumerated review threads")
    return reasons, head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int)
    parser.add_argument("expected_head", help="full, reviewed 40-character head SHA")
    parser.add_argument(
        "--merge", action="store_true", help="squash merge only if every gate passes"
    )
    args = parser.parse_args()
    if args.number < 1 or not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        parser.error("invalid PR number or head SHA")
    try:
        reasons, head = inspect(args.number, args.expected_head)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: could not verify every gate: {exc}")
        return 1
    if reasons:
        print(
            json.dumps(
                {"merge_ready": False, "number": args.number, "head": head, "reasons": reasons},
                indent=2,
            )
        )
        return 1
    if not args.merge:
        print(json.dumps({"merge_ready": True, "number": args.number, "head": head}))
        return 0
    try:
        subprocess.run(
            [
                "gh",
                "pr",
                "merge",
                str(args.number),
                "-R",
                REPO,
                "--squash",
                "--match-head-commit",
                head,
            ],
            check=True,
            timeout=90,
        )
        merged = gh("api", f"repos/{REPO}/pulls/{args.number}")
    except (subprocess.SubprocessError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"BLOCKED: merge or read-back failed: {exc}")
        return 1
    if not merged.get("merged") or merged["head"]["sha"] != head:
        print(
            "BLOCKED: merge command returned, but read-back did not confirm target head; "
            "the PR may already be merged — verify manually"
        )
        return 1
    print(
        json.dumps({"merged": True, "number": args.number, "head": head, "url": merged["html_url"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
