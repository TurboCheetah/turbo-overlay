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


def gh(*args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=90)
    return json.loads(result.stdout)


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


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
        threads = gh(
            "api",
            "graphql",
            "-f",
            f"owner={REPO.split('/')[0]}",
            "-f",
            f"name={REPO.split('/')[1]}",
            "-F",
            f"number={pr['number']}",
            "-f",
            "query=query($owner:String!, $name:String!, $number:Int!) { "
            "repository(owner:$owner, name:$name) { pullRequest(number:$number) { "
            "reviewThreads(first:100) { nodes { id isResolved } "
            "pageInfo { hasNextPage } } } } }",
        )
        thread_data = threads["data"]["repository"]["pullRequest"]["reviewThreads"]
        if thread_data["pageInfo"]["hasNextPage"]:
            raise RuntimeError(f"PR #{pr['number']} has >100 review threads; manual review needed")
        thread_state = sorted(f"{t['id']}:{int(t['isResolved'])}" for t in thread_data["nodes"])
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
                "unresolved_threads": sum(not t["isResolved"] for t in thread_data["nodes"]),
                "threads_digest": digest(";".join(thread_state)),
            }
        )
    return {"repository": REPO, "prs": output}


if __name__ == "__main__":
    try:
        print(json.dumps(snapshot(), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"PR monitor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
