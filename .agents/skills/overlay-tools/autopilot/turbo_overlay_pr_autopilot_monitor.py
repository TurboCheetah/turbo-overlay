#!/usr/bin/env python3
"""Emit stable PR state for a change-triggered Hermes review sweep."""

import hashlib
import json
import os
import subprocess
import sys
import time
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
# A thread can also exceed 100 comments; bound the per-thread comment
# pagination so an edit on a later comment page still fingerprints it.
MAX_THREAD_COMMENT_PAGES = 20
# A pathological PR (200 thread pages x up to 20 comment pages, 90s per gh
# call) could otherwise hold a single sweep for hours while the cron
# fallback fires every 10 minutes, producing overlapping sweeps and
# secondary rate-limit hits. Budget a wall-clock deadline for the whole
# sweep; a PR that cannot finish within it is surfaced as a per-PR error
# for manual review instead of monopolizing the run.
SWEEP_DEADLINE_SECONDS = 240
# Each PR also gets its own share of the sweep, so one oversized PR fails
# alone instead of exhausting the budget for every PR after it.
PR_DEADLINE_SECONDS = 60
# `gh pr list --limit N` paginates internally; cap the single request so a
# broken API cannot hang the sweep forever (50 pages = 5000 open PRs, far
# beyond this repository) and fail loudly if the cap is reached.
MAX_PR_PAGES = 50
PR_LIST_FIELDS = (
    "number,headRefOid,baseRefName,mergeable,reviewDecision,"
    "headRepositoryOwner,author,isDraft,statusCheckRollup,latestReviews"
)


def gh(*args):
    try:
        result = subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True, timeout=90
        )
    except subprocess.CalledProcessError as exc:
        # str(CalledProcessError) only carries the exit code; include gh's
        # stderr so an unattended sweep failure is actually diagnosable
        # (rate limits, auth expiry, API errors).
        raise subprocess.SubprocessError(
            f"gh {' '.join(args)} failed ({exc.returncode}): "
            f"{(exc.stderr or '').strip() or '(no stderr)'}"
        ) from exc
    return json.loads(result.stdout)


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


class DeadlineExceeded(ValueError):
    """The PR's time budget ran out before its snapshot finished."""


def _past_deadline(deadline):
    """Raise when the budget is exhausted before a PR's fetches finish.

    snapshot() catches this per PR and labels whether the PR's own budget or
    the whole sweep ran out, so the oversized PR is surfaced as an error
    entry and the rest of the sweep can proceed; the next cron run retries.
    """
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("review-thread fetch exceeded sweep deadline; manual review needed")


def fetch_thread_comments(thread, deadline=None):
    """Fetch every comment page for one review-thread node, bounded.

    The thread-list query requests only the first 100 comments per thread;
    an edit on a later comment page of an oversized thread would therefore
    not change thread_state_key. Pull the remaining pages through the
    thread's node id so the fingerprint covers the whole thread.
    """
    for _ in range(MAX_THREAD_COMMENT_PAGES):
        _past_deadline(deadline)
        comments = thread.get("comments") or {}
        page_info = comments.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return
        page = gh(
            "api",
            "graphql",
            "-f",
            "query=query($id:ID!, $cursor:String) { node(id:$id) { "
            "... on PullRequestReviewThread { comments(first:100, after:$cursor) { "
            "nodes { databaseId body } pageInfo { hasNextPage endCursor } } } } }",
            "-F",
            f"id={thread['id']}",
            *([] if not page_info.get("endCursor") else ["-F", f"cursor={page_info['endCursor']}"]),
        )
        data = page["data"]["node"]["comments"]
        thread["comments"] = {
            "nodes": (comments.get("nodes") or []) + data["nodes"],
            "pageInfo": data["pageInfo"],
        }


def fetch_threads(number, deadline=None):
    """Fetch every review-thread node for a PR, bounded by a safety cap.

    CodeRabbit flagged that a first-page-only query made threads_digest and
    unresolved_threads miss changes beyond the first 100 threads, so the
    unchanged-output check skipped reconciliation for oversized PRs. GitHub
    pages at 100; stop early when a page has no more, and flag truncation
    only if the cap is hit (the merge gate still refuses unenumerated
    threads independently). Cubic flagged that the digest also missed
    replies or edits inside an existing thread, so each thread includes a
    digest of its comments (bounded at 100; the hasNextPage marker is part
    of the digest so an oversized thread still changes it). CodeRabbit
    then flagged that only the first comments page was fetched: a later
    page edit went unnoticed, so each thread's comments are paginated in
    full (still cap-bounded).
    """
    nodes = []
    cursor = None
    for _ in range(MAX_THREAD_PAGES):
        _past_deadline(deadline)
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
            "reviewThreads(first:100, after:$cursor) { nodes { id isResolved "
            "comments(first:100) { nodes { databaseId body } "
            "pageInfo { hasNextPage endCursor } } } "
            "pageInfo { hasNextPage endCursor } } } } }",
            *([] if cursor is None else ["-F", f"cursor={cursor}"]),
        )
        data = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in data["nodes"]:
            fetch_thread_comments(thread, deadline=deadline)
        nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            return nodes, False
        cursor = data["pageInfo"]["endCursor"]
    return nodes, True


def thread_state_key(node):
    """Stable per-thread fingerprint: id, resolved flag, and comment bodies.

    Including comment content means a bot reply or an edited comment inside
    an existing thread changes the snapshot digest, waking the review sweep.
    """
    comments = sorted(
        (node.get("comments") or {}).get("nodes", []),
        key=lambda c: c.get("databaseId") or 0,
    )
    comment_digest = digest(
        "\x1f".join(f"{c.get('databaseId')}:{digest(c.get('body'))}" for c in comments)
        + "\x1e"
        + str(bool((node.get("comments") or {}).get("pageInfo", {}).get("hasNextPage")))
    )
    return f"{node['id']}:{int(node['isResolved'])}:{comment_digest}"


def fetch_bot_comments(number):
    """Hash every bot issue comment; `gh pr list --json comments` stops at one page."""
    pages = gh("api", f"repos/{REPO}/issues/{number}/comments", "--paginate", "--slurp")
    return sorted(
        (
            {"bot": c["user"]["login"], "body_hash": digest(c.get("body"))}
            for page in pages
            for c in page
            if (c.get("user") or {}).get("login") in BOT_LOGINS
        ),
        key=lambda c: (c["bot"], c["body_hash"]),
    )


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
        str(MAX_PR_PAGES * 100),
        "--json",
        PR_LIST_FIELDS,
    )
    if len(prs) >= MAX_PR_PAGES * 100:
        # The API would silently truncate at the cap; fail loudly so the
        # sweep cannot miss PRs beyond it (ValueError is caught by the
        # __main__ handler, which emits the designed failure message).
        raise ValueError(f"PR list hit safety cap at {len(prs)} open PRs; manual review needed")
    output = []
    sweep_deadline = time.monotonic() + SWEEP_DEADLINE_SECONDS
    # Newest first: if the whole sweep still runs out, the PRs left without a
    # snapshot are the oldest ones, not the new PRs the automation exists for.
    for pr in sorted(prs, key=lambda p: p["number"], reverse=True):
        if pr["number"] <= cutoff:
            continue
        pr_deadline = time.monotonic() + PR_DEADLINE_SECONDS
        deadline = min(sweep_deadline, pr_deadline)
        try:
            output.append(snapshot_pr(pr, deadline=deadline))
        except DeadlineExceeded:
            # Distinguish one slow PR (healthy sweep, retried next run) from
            # a sweep that ran out of time for every remaining PR.
            reason = (
                f"PR exceeded its {PR_DEADLINE_SECONDS}s budget"
                if pr_deadline < sweep_deadline
                else "review-thread fetch exceeded sweep deadline"
            )
            output.append(
                {
                    "number": pr["number"],
                    "head": pr.get("headRefOid"),
                    "error": f"per-PR fetch failed: {reason}; manual review needed",
                }
            )
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            # One PR's transient fetch failure must not blank the whole sweep:
            # keep the other PRs and mark the failed one so the change is
            # visible to the next monitor run.
            output.append(
                {
                    "number": pr["number"],
                    "head": pr.get("headRefOid"),
                    "error": f"per-PR fetch failed: {exc}",
                }
            )
    return {"repository": REPO, "prs": sorted(output, key=lambda p: p["number"])}


def snapshot_pr(pr, deadline=None):
    # Check before the comment fetch too, so an expired budget costs no API call.
    _past_deadline(deadline)
    reviews = sorted(
        (
            {
                "bot": r["author"]["login"],
                "state": r["state"],
                "at": r["submittedAt"],
                "body_hash": digest(r["body"]),
            }
            for r in pr["latestReviews"]
            if r.get("author") and r["author"]["login"] in BOT_LOGINS
        ),
        key=lambda r: (r["bot"], r["at"] or ""),
    )
    comments = fetch_bot_comments(pr["number"])
    checks = sorted(
        [
            {
                "name": c.get("name") or c.get("context"),
                "status": c.get("status") or c.get("state"),
                "conclusion": c.get("conclusion"),
            }
            for c in pr["statusCheckRollup"]
        ],
        key=lambda c: (str(c["name"]), str(c["status"]), str(c["conclusion"])),
    )
    thread_nodes, threads_truncated = fetch_threads(pr["number"], deadline=deadline)
    thread_state = sorted(thread_state_key(t) for t in thread_nodes)
    return {
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


if __name__ == "__main__":
    try:
        print(json.dumps(snapshot(), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"PR monitor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
