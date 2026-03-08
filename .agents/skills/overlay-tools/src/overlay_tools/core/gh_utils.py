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
    head_ref: str = ""  # Branch name the PR is from


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

    cmd = ["gh", "pr", "list", "--head", head, "--json", "number,url,state,headRefName", "--limit", "1"]
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
            head_ref=pr.get("headRefName", ""),
        )
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def gh_find_open_update_pr_for_package(
    repo_root: Path,
    *,
    category: str,
    name: str,
    base: str | None = None,
) -> PullRequestRef | None:
    """Find any open PR for a package's update branch (version-agnostic).

    Matches branches like:
    - update/{category}-{name} (new style, package-scoped)
    - update/{category}-{name}-{version} (legacy style, version-specific)

    Returns the most recent matching PR if multiple exist.
    """
    gh_require_available()

    # List all open PRs with branch info
    cmd = [
        "gh", "pr", "list",
        "--state", "open",
        "--json", "number,url,state,headRefName,baseRefName,updatedAt",
        "--limit", "100",
    ]

    result = run(cmd, cwd=repo_root, check=True, capture=True)
    output = result.stdout.strip()

    if not output or output == "[]":
        return None

    try:
        data = json.loads(output)
        if not data:
            return None

        # Branch prefix to match (both new and legacy styles)
        branch_prefix = f"update/{category}-{name}"

        matching_prs = []
        for pr in data:
            head_ref = pr.get("headRefName", "")
            base_ref = pr.get("baseRefName", "")

            # Check if branch matches our pattern
            # Exact match: update/category-name
            # Prefix match: update/category-name-* (legacy version-specific)
            if head_ref == branch_prefix or head_ref.startswith(f"{branch_prefix}-"):
                # If base filter specified, check it
                if base and base_ref != base:
                    continue
                matching_prs.append(pr)

        if not matching_prs:
            return None

        # Return the most recently updated PR (highest updatedAt)
        # This handles the edge case of multiple open PRs for the same package
        matching_prs.sort(key=lambda p: p.get("updatedAt", ""), reverse=True)
        pr = matching_prs[0]

        return PullRequestRef(
            number=pr["number"],
            url=pr["url"],
            state=pr["state"],
            head_ref=pr.get("headRefName", ""),
        )
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def gh_edit_pr(
    repo_root: Path,
    *,
    number: int,
    title: str | None = None,
    body: str | None = None,
) -> None:
    """Update an existing PR's title and/or body."""
    gh_require_available()

    cmd = ["gh", "pr", "edit", str(number)]

    if title:
        cmd.extend(["--title", title])
    if body:
        cmd.extend(["--body", body])

    if len(cmd) == 4:  # No updates specified
        return

    run(cmd, cwd=repo_root, check=True)


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
