from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from overlay_tools.core.errors import ExternalToolMissingError
from overlay_tools.core.subprocess_utils import run


@dataclass(frozen=True)
class PullRequestRef:
    number: int
    url: str
    state: str


def gh_is_available() -> bool:
    return shutil.which("gh") is not None


def gh_require_available() -> None:
    if not gh_is_available():
        raise ExternalToolMissingError(
            "gh",
            "https://cli.github.com/ or: brew install gh / pacman -S github-cli",
        )


def gh_find_pr_by_head(
    repo_root: Path,
    *,
    head: str,
    base: str | None = None,
) -> PullRequestRef | None:
    gh_require_available()

    cmd = ["gh", "pr", "list", "--head", head, "--json", "number,url,state", "--limit", "1"]
    if base:
        cmd.extend(["--base", base])

    result = run(cmd, cwd=repo_root, check=True, capture=True)
    output = result.stdout.strip()

    if not output or output == "[]":
        return None

    try:
        data = json.loads(output)
        if not data:
            return None
        pr = data[0]
        return PullRequestRef(
            number=pr["number"],
            url=pr["url"],
            state=pr["state"],
        )
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def gh_create_pr(
    repo_root: Path,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
    draft: bool = False,
    labels: list[str] | None = None,
) -> PullRequestRef:
    gh_require_available()

    cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--head",
        head,
        "--base",
        base,
    ]

    if draft:
        cmd.append("--draft")

    if labels:
        for label in labels:
            cmd.extend(["--label", label])

    result = run(cmd, cwd=repo_root, check=True, capture=True)
    pr_url = result.stdout.strip()

    view_result = run(
        ["gh", "pr", "view", head, "--json", "number,url,state"],
        cwd=repo_root,
        check=True,
        capture=True,
    )

    try:
        data = json.loads(view_result.stdout)
        return PullRequestRef(
            number=data["number"],
            url=data["url"],
            state=data["state"],
        )
    except (json.JSONDecodeError, KeyError):
        return PullRequestRef(number=0, url=pr_url, state="OPEN")


def gh_pr_url(repo_root: Path, branch: str) -> str | None:
    gh_require_available()

    result = run(
        ["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
        cwd=repo_root,
        check=False,
        capture=True,
    )

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None
