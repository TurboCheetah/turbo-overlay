#!/usr/bin/env python3
"""Pass only new same-repository PR events to the review job."""

import json
import sys

try:
    payload = json.load(sys.stdin)
except (ValueError, UnicodeDecodeError):
    payload = {}
if not isinstance(payload, dict):
    payload = {}
repo = payload.get("repository")
repo = repo if isinstance(repo, dict) else {}
pr = payload.get("pull_request")
pr = pr if isinstance(pr, dict) else {}
number = pr.get("number")
if (
    repo.get("full_name") != "TurboCheetah/turbo-overlay"
    or payload.get("action") not in {"opened", "reopened", "synchronize"}
    or type(number) is not int
    or number < 1
):
    print(json.dumps({"__hermes_ignore__": True}))
else:
    print(
        json.dumps(
            {
                "repo": "TurboCheetah/turbo-overlay",
                "number": number,
                "action": payload["action"],
            }
        )
    )
