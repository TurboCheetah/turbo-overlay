#!/usr/bin/env python3
"""Fail-closed merge gate for automated turbo-overlay PR reviews."""

import argparse
import json
import re
import subprocess

REPO = "TurboCheetah/turbo-overlay"
ALLOWED = {"TurboCheetah", "overlay-bot[bot]"}
BOTS = {"coderabbitai", "cubic-dev-ai"}


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=90)
    return json.loads(result.stdout)


def inspect(number, expected_head):
    pr = gh("api", f"repos/{REPO}/pulls/{number}")
    head = pr["head"]["sha"]
    reasons = []
    if pr["state"] != "open" or pr.get("draft"):
        reasons.append("PR is closed or draft")
    if head != expected_head:
        reasons.append("head changed since review; review new commit")
    if pr["base"]["ref"] != "master" or pr["base"]["repo"]["full_name"] != REPO:
        reasons.append("unexpected target repository or branch")
    if pr["user"]["login"] not in ALLOWED or (pr["head"]["repo"] or {}).get("full_name") != REPO:
        reasons.append("not an authorized same-repository author/head; manual approval required")
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

    reviews = gh("api", f"repos/{REPO}/pulls/{number}/reviews", "--paginate")
    latest = {}
    for review in reviews:
        user = review["user"]["login"].lower().removesuffix("[bot]")
        if user in BOTS:
            latest[user] = review
        if review["state"] == "CHANGES_REQUESTED" and review["commit_id"] == head:
            reasons.append(f"changes requested on current head by {review['user']['login']}")
    for bot in BOTS:
        review = latest.get(bot)
        if (
            not review
            or review["commit_id"] != head
            or review["state"] not in {"APPROVED", "COMMENTED"}
        ):
            reasons.append(f"{bot} has not completed a substantive current-head review")
        elif "rate limit" in (review.get("body") or "").lower():
            reasons.append(f"{bot} review was rate-limited")

    comments = gh("api", f"repos/{REPO}/issues/{number}/comments", "--paginate")
    rabbit = latest.get("coderabbitai")
    for comment in comments:
        user = comment["user"]["login"].lower().removesuffix("[bot]")
        if user == "coderabbitai" and rabbit:
            text = (comment.get("body") or "").lower()
            if ("skip review" in text or "rate limit" in text) and comment["updated_at"] >= rabbit[
                "submitted_at"
            ]:
                reasons.append(
                    "CodeRabbit status comment says the current review was skipped or rate-limited"
                )
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
    except (KeyError, ValueError, subprocess.SubprocessError) as exc:
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
    if not merged.get("merged") or merged["head"]["sha"] != head:
        raise RuntimeError("merge command returned, but read-back did not confirm target head")
    print(
        json.dumps({"merged": True, "number": args.number, "head": head, "url": merged["html_url"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
