#!/usr/bin/env python3
"""Pass only new same-repository PR events to the review job."""

import json
import sys

payload = json.load(sys.stdin)
repo = payload.get("repository") or {}
pr = payload.get("pull_request") or {}
if (
    repo.get("full_name") != "TurboCheetah/turbo-overlay"
    or payload.get("action") not in {"opened", "reopened", "synchronize"}
    or not isinstance(pr.get("number"), int)
    or pr["number"] < 1
):
    print(json.dumps({"__hermes_ignore__": True}))
else:
    print(
        json.dumps(
            {
                "repo": "TurboCheetah/turbo-overlay",
                "number": pr["number"],
                "action": payload["action"],
            }
        )
    )
