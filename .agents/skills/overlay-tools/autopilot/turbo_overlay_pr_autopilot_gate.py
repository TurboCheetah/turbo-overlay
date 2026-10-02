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
CUBIC_CLEAN = re.compile(r"\b0 issues found\b")
# CodeRabbit publishes a substantive current-head review either as a review
# submission (when a run produces actionable comments) or by editing its
# persistent summary comment, which then embeds a final_review_risk_coverage
# marker naming the exact commit it covered. Match the marker's JSON fields,
# not free text.
RABBIT_COVERED = re.compile(
    r"final_review_risk_coverage:\{\"sourceCommitId\":\"([0-9a-f]{40})\","
    r"\"coveredCommitId\":\"([0-9a-f]{40})\",\"kind\":\"reviewed\"\}"
)


def _visible_body(body):
    """Strip HTML marker comments so a marker-only review is not substantive."""
    return re.sub(r"<!--.*?-->", "", body or "", flags=re.DOTALL).strip()


def gh(*args):
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=90
        )
    except subprocess.CalledProcessError as exc:
        # str(CalledProcessError) omits stderr; re-raise so an unattended
        # gate failure is diagnosable (rate limits, auth expiry, API errors).
        raise subprocess.SubprocessError(
            f"gh {' '.join(args)} failed ({exc.returncode}): "
            f"{(exc.stderr or '').strip() or '(no stderr)'}"
        ) from exc
    return json.loads(result.stdout)


def flatten_pages(items):
    """gh api --paginate --slurp returns a list of page arrays; merge them."""
    if all(isinstance(i, list) for i in items):
        return [item for page in items for item in page]
    return items


def cubic_clean_followup(number, head, reviewed):
    """Accept Cubic's clean check run on `head` when its last review is older.

    Cubic posts no GitHub review for a follow-up commit where it finds nothing
    and answers re-review requests with "no new changes", so a strict
    current-head review requirement would block such PRs forever. Its check run
    on the exact head reports the issue count, so accept only its latest run
    there when it completed successfully with "0 issues found", and only if the
    older review covered an earlier commit of this PR.
    """
    pages = gh("api", f"repos/{REPO}/commits/{head}/check-runs", "--paginate", "--slurp")
    runs = [
        run
        for page in pages
        for run in page["check_runs"]
        if (run.get("app") or {}).get("slug") == "cubic-dev-ai"
    ]
    if not runs:
        return False
    run = max(runs, key=lambda r: r["id"])
    if (
        run.get("head_sha") != head
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not CUBIC_CLEAN.search((run.get("output") or {}).get("summary") or "")
    ):
        return False
    commits = flatten_pages(
        gh("api", f"repos/{REPO}/pulls/{number}/commits", "--paginate", "--slurp")
    )
    return reviewed in {c["sha"] for c in commits}


def coderabbit_comment_coverage(number, head, comments, review):
    """Accept CodeRabbit's edited summary comment as a current-head review.

    CodeRabbit posts a review submission only when a run produces actionable
    comments; a clean run only edits its persistent summary comment, which
    then embeds a final_review_risk_coverage marker naming the exact commit
    it covered. Accept the marker only on the bot's own comment updated after
    the older submission, when it names the current head with kind
    "reviewed", and when the older review covered an earlier commit of this
    PR. A rate-limit marker in the same comment disqualifies it.
    """
    submitted_at = review.get("submitted_at") or ""
    candidates = [
        c
        for c in comments
        # Exact bot login: the marker only means something from CodeRabbit's app.
        if (c.get("user") or {}).get("login") == "coderabbitai[bot]"
        and (c.get("updated_at") or "") >= submitted_at
        and not RABBIT_RATE_LIMIT.search(c.get("body") or "")
        and RABBIT_COVERED.search(c.get("body") or "")
    ]
    if not candidates:
        return False
    # The summary comment carrying the marker is edited in place; later bot
    # replies ("Full review finished") can have newer updated_at without the
    # marker, so pick the newest comment that actually embeds the marker.
    comment = max(candidates, key=lambda c: c.get("updated_at") or "")
    m = RABBIT_COVERED.search(comment.get("body") or "")
    assert m is not None  # candidates are pre-filtered to marker-bearing comments
    source, target = m.groups()
    if source != head or target != head:
        return False
    commits = flatten_pages(
        gh("api", f"repos/{REPO}/pulls/{number}/commits", "--paginate", "--slurp")
    )
    return review.get("commit_id") in {c["sha"] for c in commits}


def inspect(number, expected_head, *, agent_reviewed=False):
    pr = gh("api", f"repos/{REPO}/pulls/{number}")
    reasons = []
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
    # The poll can observe a PR after it becomes draft or closed; the open/
    # draft gates must apply to the freshest response, not the first read.
    if pr["state"] != "open" or pr.get("draft"):
        reasons.append("PR is closed or draft")
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
        # A thread reply is stored as a COMMENTED review with an empty body, so
        # it must not displace the bot's real review. Other marker-only reviews
        # are not substantive either (a bot with nothing else stays blocked),
        # but a rate-limit marker is kept so it is reported as such.
        body = review.get("body") or ""
        if user in BOTS and (
            review["state"] != "COMMENTED" or _visible_body(body) or RABBIT_RATE_LIMIT.search(body)
        ):
            latest[user] = review
        # GitHub keeps a request for changes in force across later commits until
        # the reviewer approves or it is dismissed; comments do not clear it.
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            verdicts[login] = review["state"]
    for login, state in sorted(verdicts.items()):
        if state == "CHANGES_REQUESTED":
            reasons.append(f"changes requested by {login} and not approved or dismissed")

    comments = flatten_pages(
        gh("api", f"repos/{REPO}/issues/{number}/comments", "--paginate", "--slurp")
    )
    # The agent's own review can replace bot reviews that are unavailable for
    # any authorized same-repo PR (CodeRabbit holds no seat in this small
    # repository; Cubic may decline bot-authored PRs). All GitHub, CI,
    # authorization and thread gates remain.
    for bot in sorted(BOTS) if not agent_reviewed else ():
        review = latest.get(bot)
        if not review or review["state"] not in {"APPROVED", "COMMENTED"}:
            reasons.append(f"{bot} has not completed a substantive current-head review")
        elif RABBIT_RATE_LIMIT.search(review.get("body") or ""):
            # CodeRabbit embeds its rate-limit notice in an HTML marker comment.
            # Match only that marker: a review body explaining or quoting the
            # rate-limit check must not be mistaken for a rate-limited review.
            reasons.append(f"{bot} review was rate-limited")
        elif (
            review["commit_id"] != head
            and not (
                bot == "cubic-dev-ai" and cubic_clean_followup(number, head, review["commit_id"])
            )
            and not (
                bot == "coderabbitai"
                and coderabbit_comment_coverage(number, head, comments, review)
            )
        ):
            reasons.append(f"{bot} has not completed a substantive current-head review")

    rabbit = latest.get("coderabbitai")
    for comment in comments:
        user = ((comment.get("user") or {}).get("login") or "").lower().removesuffix("[bot]")
        if (
            not agent_reviewed
            and user == "coderabbitai"
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
        "--agent-reviewed",
        action="store_true",
        help="assert independent review of this exact head; bot reviews become advisory",
    )
    parser.add_argument(
        "--merge", action="store_true", help="squash merge only if every gate passes"
    )
    args = parser.parse_args()
    if args.number < 1 or not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        parser.error("invalid PR number or head SHA")
    try:
        reasons, head = inspect(args.number, args.expected_head, agent_reviewed=args.agent_reviewed)
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
    # The first inspect ran moments ago, but a same-head change (new
    # comment, thread resolution, review decision) can land before the
    # merge command, and --match-head-commit pins only the SHA. Re-run
    # every gate immediately before merging so a same-head review change
    # cannot be merged unnoticed.
    try:
        reasons, head = inspect(args.number, args.expected_head, agent_reviewed=args.agent_reviewed)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: could not verify every gate before merge: {exc}")
        return 1
    if reasons:
        print(
            json.dumps(
                {"merge_ready": False, "number": args.number, "head": head, "reasons": reasons},
                indent=2,
            )
        )
        return 1
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
