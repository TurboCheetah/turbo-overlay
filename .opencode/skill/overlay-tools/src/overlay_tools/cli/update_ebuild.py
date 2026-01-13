from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from overlay_tools.core.ebuilds import (
    find_ebuilds,
    select_latest_ebuild,
    update_ebuild_var,
)
from overlay_tools.core.errors import VersionError
from overlay_tools.core.git_utils import (
    git_add,
    git_branch_exists,
    git_checkout_branch,
    git_commit,
    git_current_branch,
    git_default_branch,
    git_has_changes,
    git_push,
    git_root,
    is_git_repo,
)
from overlay_tools.core.logging import Logger, set_logger
from overlay_tools.core.subprocess_utils import run_ebuild_manifest
from overlay_tools.core.versions import normalize_gentoo_version


def generate_branch_name(category: str, name: str, version: str) -> str:
    safe_version = version.replace(".", "-").replace("_", "-")
    return f"update/{category}-{name}-{safe_version}"


def generate_pr_body(
    category: str,
    name: str,
    old_version: str,
    new_version: str,
    my_pv: str | None = None,
    upstream_url: str | None = None,
    dropped_version: str | None = None,
) -> str:
    lines = [
        "## Summary",
        "",
        f"- Bump `{category}/{name}` to `{new_version}`",
    ]

    if dropped_version and dropped_version != old_version:
        lines.append(f"- Drop old version `{dropped_version}`")

    if my_pv:
        lines.append(f'- Set `MY_PV="{my_pv}"`')

    lines.append("")
    lines.append("## Details")
    lines.append("")
    lines.append(f"- **Package**: `{category}/{name}`")
    lines.append(f"- **Previous**: `{old_version}`")
    lines.append(f"- **New**: `{new_version}`")

    if upstream_url:
        lines.append(f"- **Upstream**: {upstream_url}")

    lines.append("")
    lines.append("---")
    lines.append("*This PR was generated automatically by overlay-tools.*")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="update-ebuild",
        description="Update a Gentoo ebuild to a new version",
    )
    parser.add_argument(
        "-v",
        "--version",
        required=True,
        metavar="VERSION",
        help="New version (Gentoo format: 1.2.3, 1.2.3_p1, 1.2.3_pre)",
    )
    parser.add_argument(
        "-m",
        "--my-pv",
        metavar="MY_PV",
        help="Set MY_PV for upstream version mapping (e.g., for warp-bin)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Preview changes without applying",
    )
    parser.add_argument(
        "-s",
        "--skip-git",
        action="store_true",
        help="Skip git operations",
    )
    parser.add_argument(
        "-l",
        "--lenient",
        action="store_true",
        help="Allow non-standard version formats",
    )
    parser.add_argument(
        "-k",
        "--keep-old",
        action="store_true",
        help="Keep old ebuild (don't remove oldest version)",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Skip Manifest update (for CI without ebuild command)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Non-interactive mode, assume yes to prompts",
    )
    parser.add_argument(
        "--pr",
        action="store_true",
        help="Create a PR after committing (implies --yes)",
    )
    parser.add_argument(
        "--base",
        metavar="BRANCH",
        help="Base branch for PR (default: auto-detect or master)",
    )
    parser.add_argument(
        "--branch",
        metavar="BRANCH",
        help="Override the feature branch name",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create PR as draft",
    )
    parser.add_argument(
        "--upstream-url",
        metavar="URL",
        help="Upstream release URL for PR body",
    )
    parser.add_argument(
        "package_path",
        metavar="PACKAGE_PATH",
        help="Path to package directory (e.g., media-video/hayase-bin)",
    )

    args = parser.parse_args(argv)

    if args.pr:
        args.yes = True

    log = Logger(verbose=True, quiet=False)
    set_logger(log)

    try:
        normalized_version = normalize_gentoo_version(args.version, lenient=args.lenient)
    except VersionError as e:
        log.error(str(e))
        if not args.lenient:
            log.info("Hint: Use -l/--lenient for non-standard version formats")
        return 1

    pkg_path = Path(args.package_path).resolve()
    if not pkg_path.is_dir():
        log.error(f"Not a directory: {pkg_path}")
        return 1

    ebuilds = find_ebuilds(pkg_path)
    if not ebuilds:
        log.error(f"No ebuilds found in {pkg_path}")
        return 1

    latest = select_latest_ebuild(ebuilds, exclude_live=True)
    if not latest:
        log.error("No non-live ebuilds found")
        return 1

    oldest = min(ebuilds, key=lambda e: e.pv) if len(ebuilds) > 1 else latest
    category = pkg_path.parent.name
    name = pkg_path.name

    new_filename = f"{latest.pn}-{normalized_version}.ebuild"
    new_path = pkg_path / new_filename

    if new_path.exists():
        log.error(f"Ebuild already exists: {new_filename}")
        return 1

    repo_root = git_root(pkg_path) if is_git_repo(pkg_path) else pkg_path
    base_branch = args.base or git_default_branch(repo_root)
    feature_branch = args.branch or generate_branch_name(category, name, normalized_version)
    original_branch = git_current_branch(repo_root) if is_git_repo(pkg_path) else None

    log.rule("Update Ebuild")
    log.package(category, name, "version bump")
    log.version_change(latest.pv, normalized_version)

    if args.pr:
        log.info(f"Branch: {feature_branch}")
        log.info(f"Base: {base_branch}")

    if args.dry_run:
        log.info(f"Would copy {latest.path.name} → {new_filename}")
        if args.my_pv:
            log.info(f'Would set MY_PV="{args.my_pv}"')
        if not args.keep_old and oldest != latest:
            log.info(f"Would remove {oldest.path.name}")
        if args.skip_manifest:
            log.info("Would skip Manifest update")
        else:
            log.info("Would update Manifest")
        if args.pr:
            log.info(f"Would create branch: {feature_branch}")
            log.info(f"Would push to origin/{feature_branch}")
            log.info(f"Would create PR: {category}/{name}: add {normalized_version}")
        elif not args.skip_git and is_git_repo(pkg_path):
            msg = f"{category}/{name}: add {normalized_version}"
            if not args.keep_old and oldest != latest:
                msg += f", drop {oldest.pv}"
            log.info(f"Would commit: {msg}")
        log.success("Dry run complete - no changes made")
        return 0

    if args.pr and is_git_repo(pkg_path):
        if git_branch_exists(feature_branch, repo_root):
            log.warning(f"Branch {feature_branch} already exists locally")
            git_checkout_branch(feature_branch, repo_root)
        else:
            log.info(f"Creating branch: {feature_branch}")
            git_checkout_branch(feature_branch, repo_root, create=True, start_point=base_branch)

    log.info(f"Copying {latest.path.name} → {new_filename}")
    shutil.copy2(latest.path, new_path)

    if args.my_pv:
        if update_ebuild_var(new_path, "MY_PV", args.my_pv):
            log.success(f'Updated MY_PV="{args.my_pv}"')
        else:
            log.warning("MY_PV not found in ebuild - variable not updated")

    deleted_oldest: Path | None = None
    dropped_version: str | None = None
    if not args.keep_old and oldest != latest:
        log.info(f"Removing {oldest.path.name}")
        oldest.path.unlink()
        deleted_oldest = oldest.path
        dropped_version = oldest.pv

    if args.skip_manifest:
        log.info("Skipping Manifest update (--skip-manifest)")
    else:
        log.info("Updating Manifest...")
        try:
            run_ebuild_manifest(new_path)
            log.success("Manifest updated")
        except Exception as e:
            log.warning(f"Manifest update failed: {e}")
            log.info(f"Run manually: ebuild {new_path} manifest")

    if args.skip_git or not is_git_repo(pkg_path):
        log.rule()
        log.success("Done!")
        return 0

    commit_msg = f"{category}/{name}: add {normalized_version}"
    if dropped_version:
        commit_msg += f", drop {dropped_version}"

    log.rule("Git")

    paths_to_add = [new_path, pkg_path / "Manifest"]
    if deleted_oldest:
        paths_to_add.append(deleted_oldest)

    should_commit = args.yes or args.pr

    if not should_commit:
        try:
            from rich.prompt import Confirm

            should_commit = Confirm.ask("Commit these changes?", default=False)
        except ImportError:
            response = input("Commit these changes? [y/N] ").strip().lower()
            should_commit = response == "y"

    if should_commit:
        git_add(paths_to_add, repo_root)
        git_commit(commit_msg, repo_root)
        log.success(f"Committed: {commit_msg}")
    else:
        log.info("Changes not committed")
        return 0

    if not args.pr:
        log.rule()
        log.success("Done!")
        return 0

    log.rule("Pull Request")

    from overlay_tools.core.gh_utils import (
        gh_create_pr,
        gh_find_pr_by_head,
        gh_require_available,
    )

    try:
        gh_require_available()
    except Exception as e:
        log.error(str(e))
        log.info("Commit was created but PR was not. Push manually and create PR.")
        return 1

    log.info(f"Pushing {feature_branch}...")
    try:
        git_push(repo_root, branch=feature_branch, set_upstream=True)
        log.success("Pushed to origin")
    except Exception as e:
        log.error(f"Push failed: {e}")
        return 1

    existing_pr = gh_find_pr_by_head(repo_root, head=feature_branch, base=base_branch)
    if existing_pr:
        log.success(f"PR already exists: {existing_pr.url}")
        return 0

    pr_title = commit_msg
    pr_body = generate_pr_body(
        category=category,
        name=name,
        old_version=latest.pv,
        new_version=normalized_version,
        my_pv=args.my_pv,
        upstream_url=args.upstream_url,
        dropped_version=dropped_version,
    )

    log.info("Creating PR...")
    try:
        pr = gh_create_pr(
            repo_root,
            title=pr_title,
            body=pr_body,
            head=feature_branch,
            base=base_branch,
            draft=args.draft,
        )
        log.success(f"PR created: {pr.url}")
    except Exception as e:
        log.error(f"PR creation failed: {e}")
        log.info(f"Branch {feature_branch} was pushed. Create PR manually.")
        return 1

    if original_branch and original_branch != feature_branch:
        log.info(f"Switching back to {original_branch}")
        git_checkout_branch(original_branch, repo_root)

    log.rule()
    log.success("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
