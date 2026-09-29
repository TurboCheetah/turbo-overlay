#!/usr/bin/env python3
"""Emit stable PR state for a change-triggered Hermes review sweep."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = "TurboCheetah/turbo-overlay"
CONFIG = Path(
    os.environ.get(
        "TURBO_OVERLAY_AUTOPILOT_CONFIG",
        str(Path.home() / ".hermes/data/turbo_overlay_pr_autopilot.json"),
    )
)
BOT_LOGINS = {"coderabbitai", "coderabbitai[bot]", "cubic-dev-ai", "cubic-dev-ai[bot]"}
# GitHub pages review threads at 100; this bounds a pathological PR (20k threads).
MAX_THREAD_PAGES = 200


def gh(*args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=90)
    return json.loads(result.stdout)


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def fetch_threads(number):
    """Fetch every review-thread node for a PR, bounded by a safety cap.

    CodeRabbit flagged that a first-page-only query made threads_digest and
    unresolved_threads miss changes beyond the first 100 threads, so the
    unchanged-output check skipped reconciliation for oversized PRs. GitHub
    pages at 100; stop early when a page has no more, and flag truncation
    only if the cap is hit (the merge gate still refuses unenumerated
    threads independently).
    """
    nodes = []
    cursor = None
    for _ in range(MAX_THREAD_PAGES):
        page = gh(
            "api",
            "graphql",
            "-f",
            f"owner={REPO.split('/')[0]}",
            "-f",
            f"name={REPO.split('/')[1]}",
            "-F",
            f"number={number}",
            "-f",
            "query=query($owner:String!, $name:String!, $number:Int!, $cursor:String) { "
            "repository(owner:$owner, name:$name) { pullRequest(number:$number) { "
            "reviewThreads(first:100, after:$cursor) { nodes { id isResolved } "
            "pageInfo { hasNextPage endCursor } } } } }",
            *([] if cursor is None else ["-F", f"cursor={cursor}"]),
        )
        data = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            return nodes, False
        cursor = data["pageInfo"]["endCursor"]
    return nodes, True


def snapshot():
    cutoff = int(json.loads(CONFIG.read_text())["created_after_pr"])
    prs = gh(
        "pr",
        "list",
        "-R",
        REPO,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        "number,headRefOid,baseRefName,mergeable,reviewDecision,"
        "headRepositoryOwner,author,isDraft,statusCheckRollup,"
        "latestReviews,comments",
    )
    output = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        if pr["number"] <= cutoff:
            continue
        reviews = [
            {
                "bot": r["author"]["login"],
                "state": r["state"],
                "at": r["submittedAt"],
                "body_hash": digest(r["body"]),
            }
            for r in pr["latestReviews"]
            if r.get("author") and r["author"]["login"] in BOT_LOGINS
        ]
        comments = [
            {"bot": c["author"]["login"], "body_hash": digest(c["body"])}
            for c in pr["comments"]
            if c.get("author") and c["author"]["login"] in BOT_LOGINS
        ]
        checks = sorted(
            [
                {
                    "name": c.get("name") or c.get("context"),
                    "status": c.get("status") or c.get("state"),
                    "conclusion": c.get("conclusion"),
                }
                for c in pr["statusCheckRollup"]
            ],
            key=lambda c: str(c["name"]),
        )
        thread_nodes, threads_truncated = fetch_threads(pr["number"])
        thread_state = sorted(f"{t['id']}:{int(t['isResolved'])}" for t in thread_nodes)
        output.append(
            {
                "number": pr["number"],
                "head": pr["headRefOid"],
                "base": pr["baseRefName"],
                "mergeable": pr.get("mergeable"),
                "review_decision": pr.get("reviewDecision"),
                "author": (pr.get("author") or {}).get("login"),
                "head_owner": (pr.get("headRepositoryOwner") or {}).get("login"),
                "draft": pr["isDraft"],
                "checks": checks,
                "bot_reviews": reviews,
                "bot_comments": comments,
                "unresolved_threads": sum(not t["isResolved"] for t in thread_nodes),
                "threads_digest": digest(";".join(thread_state)),
                "threads_truncated": threads_truncated,
            }
        )
    return {"repository": REPO, "prs": output}


if __name__ == "__main__":
    try:
        print(json.dumps(snapshot(), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"PR monitor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
