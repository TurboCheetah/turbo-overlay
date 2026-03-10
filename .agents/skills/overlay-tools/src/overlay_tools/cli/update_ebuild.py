from __future__ import annotations

import argparse
import shutil
import sys
from functools import cmp_to_key
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
    git_fetch_branch,
    git_has_changes,
    git_push,
    git_root,
    is_git_repo,
)
from overlay_tools.core.logging import Logger, set_logger
from overlay_tools.core.subprocess_utils import run_ebuild_manifest, run_egencache_update
from overlay_tools.core.versions import compare_versions, normalize_gentoo_version


def generate_branch_name(category: str, name: str) -> str:
    """Generate a package-scoped branch name (version-agnostic).

    This allows reusing the same branch/PR for multiple version updates.
    """
    return f"update/{category}-{name}"


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


def read_repo_name(repo_root: Path) -> str | None:
    repo_name_path = repo_root / "profiles" / "repo_name"
    if not repo_name_path.is_file():
        return None

    repo_name = repo_name_path.read_text(encoding="utf-8").strip()
    return repo_name or None


def metadata_cache_path(repo_root: Path, category: str, name: str, version: str) -> Path:
    return repo_root / "metadata" / "md5-cache" / category / f"{name}-{version}"


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

    oldest = (
        min(ebuilds, key=cmp_to_key(lambda a, b: compare_versions(a.pv, b.pv)))
        if len(ebuilds) > 1
        else latest
    )
    category = pkg_path.parent.name
    name = pkg_path.name

    new_filename = f"{latest.pn}-{normalized_version}.ebuild"
    new_path = pkg_path / new_filename

    if new_path.exists():
        log.error(f"Ebuild already exists: {new_filename}")
        return 1

    repo_root = git_root(pkg_path) if is_git_repo(pkg_path) else pkg_path
    is_git = is_git_repo(pkg_path)
    base_branch = args.base or git_default_branch(repo_root)
    feature_branch = args.branch or generate_branch_name(category, name)
    original_branch = git_current_branch(repo_root) if is_git else None

    log.rule("Update Ebuild")
    log.package(category, name, "version bump")
    log.version_change(latest.pv, normalized_version)
    if args.pr:
        if is_git:
            log.info(f"Git repository: {repo_root}")
        else:
            log.warning(f"Not in a git repository: {repo_root}")
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
        repo_name = read_repo_name(repo_root)
        if repo_name:
            log.info("Would update metadata/md5-cache")
            if not args.keep_old and oldest != latest:
                old_cache_path = metadata_cache_path(repo_root, category, name, oldest.pv)
                log.info(f"Would remove {old_cache_path.relative_to(repo_root)}")
        else:
            log.info("Would skip metadata/md5-cache update (repo name unavailable)")
        if args.pr:
            from overlay_tools.core.gh_utils import (
                gh_find_open_update_pr_for_package,
                gh_is_available,
            )

            dry_run_existing_pr = None
            if gh_is_available() and is_git:
                try:
                    dry_run_existing_pr = gh_find_open_update_pr_for_package(
                        repo_root,
                        category=category,
                        name=name,
                        base=base_branch,
                    )
                except Exception as e:
                    log.warning(f"Could not check for existing PRs: {e}")

            if dry_run_existing_pr:
                log.info(
                    f"Found existing PR #{dry_run_existing_pr.number}: {dry_run_existing_pr.url}"
                )
                log.info(f"Would reuse branch: {dry_run_existing_pr.head_ref}")
                log.info(f"Would push to origin/{dry_run_existing_pr.head_ref}")
                log.info(f"Would update PR: {category}/{name}: add {normalized_version}")
            else:
                log.info(f"Would create branch: {feature_branch}")
                log.info(f"Would push to origin/{feature_branch}")
                log.info(f"Would create PR: {category}/{name}: add {normalized_version}")
        elif not args.skip_git and is_git:
            msg = f"{category}/{name}: add {normalized_version}"
            if not args.keep_old and oldest != latest:
                msg += f", drop {oldest.pv}"
            log.info(f"Would commit: {msg}")
        log.success("Dry run complete - no changes made")
        return 0

    # Track whether we're updating an existing PR
    existing_pr_ref = None

    if args.pr and is_git:
        # Check for existing open PR for this package (any version)
        from overlay_tools.core.gh_utils import (
            gh_find_open_update_pr_for_package,
            gh_require_available,
        )

        from overlay_tools.core.errors import ExternalToolMissingError

        try:
            gh_require_available()
            existing_pr_ref = gh_find_open_update_pr_for_package(
                repo_root,
                category=category,
                name=name,
                base=base_branch,
            )
            if existing_pr_ref:
                feature_branch = existing_pr_ref.head_ref
                log.info(f"Found existing PR #{existing_pr_ref.number}: {existing_pr_ref.url}")
                log.info(f"Reusing branch: {feature_branch}")
        except ExternalToolMissingError:
            pass
        except Exception as e:
            log.warning(f"Could not check for existing PRs: {e}")
            log.info("Will create new PR if none exists for this branch")

        if git_branch_exists(feature_branch, repo_root):
            log.info(f"Checking out existing branch: {feature_branch}")
            git_checkout_branch(feature_branch, repo_root)
        elif git_branch_exists(feature_branch, repo_root, remote=True):
            # Branch exists on remote but not locally (CI scenario)
            log.info(f"Fetching remote branch: {feature_branch}")
            git_checkout_branch(feature_branch, repo_root, track_remote=True)
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
    deleted_oldest_cache: Path | None = None
    if not args.keep_old and oldest != latest:
        log.info(f"Removing {oldest.path.name}")
        oldest.path.unlink()
        deleted_oldest = oldest.path
        dropped_version = oldest.pv
        deleted_oldest_cache = metadata_cache_path(repo_root, category, name, oldest.pv)
        if deleted_oldest_cache.exists():
            log.info(f"Removing {deleted_oldest_cache.relative_to(repo_root)}")
            deleted_oldest_cache.unlink()

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

    new_cache_path = metadata_cache_path(repo_root, category, name, normalized_version)
    repo_name = read_repo_name(repo_root)
    if repo_name:
        log.info("Updating metadata/md5-cache...")
        try:
            run_egencache_update(repo_root, repo_name=repo_name, atom=f"{category}/{name}")
            if new_cache_path.exists():
                log.success("metadata/md5-cache updated")
            else:
                log.warning(
                    f"egencache completed but did not create {new_cache_path.relative_to(repo_root)}"
                )
        except Exception as e:
            log.warning(f"metadata/md5-cache update failed: {e}")
            log.info(f"Run manually: egencache --repo {repo_name} --update {category}/{name}")
    else:
        log.warning("Could not determine repo name from profiles/repo_name")
        log.info("Skipping metadata/md5-cache update")

    if args.skip_git or not is_git:
        log.rule()
        if args.skip_git:
            log.info("Git operations skipped (--skip-git)")
        else:
            log.warning(f"Not a git repository: {pkg_path}")
            log.info("Changes were made but not committed. Run manually from a git repo.")
        log.success("Done!")
        return 0

    commit_msg = f"{category}/{name}: add {normalized_version}"
    if dropped_version:
        commit_msg += f", drop {dropped_version}"

    log.rule("Git")

    paths_to_add = [new_path, pkg_path / "Manifest"]
    if new_cache_path.exists():
        paths_to_add.append(new_cache_path)
    if deleted_oldest:
        paths_to_add.append(deleted_oldest)
    if deleted_oldest_cache and not deleted_oldest_cache.exists():
        paths_to_add.append(deleted_oldest_cache)

    should_commit = args.yes or args.pr

    if not should_commit:
        if not sys.stdin.isatty():
            log.info("Non-interactive mode detected, skipping commit (use -y to commit)")
            should_commit = False
        else:
            try:
                from rich.prompt import Confirm

                should_commit = Confirm.ask("Commit these changes?", default=False)
            except (ImportError, EOFError):
                try:
                    response = input("Commit these changes? [y/N] ").strip().lower()
                    should_commit = response == "y"
                except EOFError:
                    should_commit = False

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
        gh_edit_pr,
        gh_require_available,
    )

    try:
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

        if existing_pr_ref:
            log.info(f"Updating existing PR #{existing_pr_ref.number}...")
            try:
                gh_edit_pr(
                    repo_root,
                    number=existing_pr_ref.number,
                    title=pr_title,
                    body=pr_body,
                )
                log.success(f"PR updated: {existing_pr_ref.url}")
            except Exception as e:
                log.warning(f"PR update failed: {e}")
                log.info(f"Changes were pushed. Update PR manually: {existing_pr_ref.url}")
        else:
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

        log.rule()
        log.success("Done!")
        return 0
    finally:
        if original_branch and original_branch != feature_branch:
            log.info(f"Switching back to {original_branch}")
            git_checkout_branch(original_branch, repo_root)


if __name__ == "__main__":
    sys.exit(main())
