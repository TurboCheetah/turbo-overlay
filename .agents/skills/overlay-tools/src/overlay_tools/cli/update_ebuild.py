from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from overlay_tools.core.ebuilds import EbuildName, find_ebuilds, select_latest_ebuild, update_ebuild_var
from overlay_tools.core.errors import ExternalToolMissingError, VersionError
from overlay_tools.core.git_utils import (
    git_add,
    git_branch_exists,
    git_checkout_branch,
    git_commit,
    git_current_branch,
    git_default_branch,
    git_push,
    git_root,
    is_git_repo,
)
from overlay_tools.core.logging import Logger, set_logger
from overlay_tools.core.overlay import metadata_cache_path, read_repo_name, select_ebuild_to_drop
from overlay_tools.core.subprocess_utils import run_ebuild_manifest, run_egencache_update
from overlay_tools.core.versions import normalize_gentoo_version


@dataclass(frozen=True)
class UpdateContext:
    category: str
    name: str
    pkg_path: Path
    repo_root: Path
    is_git: bool
    latest: EbuildName
    ebuilds: list[EbuildName]
    base_branch: str
    feature_branch: str
    original_branch: str | None


@dataclass(frozen=True)
class UpdatePlan:
    context: UpdateContext
    normalized_version: str
    new_filename: str
    new_path: Path
    repo_name: str | None
    new_cache_path: Path
    drop_ebuild: EbuildName | None
    drop_cache_path: Path | None
    commit_message: str


@dataclass(frozen=True)
class AppliedChanges:
    deleted_ebuild_path: Path | None
    deleted_cache_path: Path | None


@dataclass(frozen=True)
class RefreshedArtifacts:
    paths: tuple[Path, ...]


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("-s", "--skip-git", action="store_true", help="Skip git operations")
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
        help="Keep old ebuild (don't remove old versions)",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Skip Manifest update (for CI without ebuild command)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode, assume yes to prompts")
    parser.add_argument("--pr", action="store_true", help="Create a PR after committing (implies --yes)")
    parser.add_argument("--base", metavar="BRANCH", help="Base branch for PR (default: auto-detect or master)")
    parser.add_argument("--branch", metavar="BRANCH", help="Override the feature branch name")
    parser.add_argument("--draft", action="store_true", help="Create PR as draft")
    parser.add_argument("--upstream-url", metavar="URL", help="Upstream release URL for PR body")
    parser.add_argument(
        "package_path",
        metavar="PACKAGE_PATH",
        help="Path to package directory (e.g., media-video/hayase-bin)",
    )
    return parser


def generate_branch_name(category: str, name: str) -> str:
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

    lines.extend(
        [
            "",
            "## Details",
            "",
            f"- **Package**: `{category}/{name}`",
            f"- **Previous**: `{old_version}`",
            f"- **New**: `{new_version}`",
        ]
    )

    if upstream_url:
        lines.append(f"- **Upstream**: {upstream_url}")

    lines.extend(["", "---", "*This PR was generated automatically by overlay-tools.*"])
    return "\n".join(lines)


def build_context(args: argparse.Namespace, normalized_version: str) -> UpdateContext:
    pkg_path = Path(args.package_path).resolve()
    if not pkg_path.is_dir():
        raise ValueError(f"Not a directory: {pkg_path}")

    ebuilds = find_ebuilds(pkg_path)
    if not ebuilds:
        raise ValueError(f"No ebuilds found in {pkg_path}")

    latest = select_latest_ebuild(ebuilds, exclude_live=True)
    if not latest:
        raise ValueError("No non-live ebuilds found")

    new_filename = f"{latest.pn}-{normalized_version}.ebuild"
    if (pkg_path / new_filename).exists():
        raise ValueError(f"Ebuild already exists: {new_filename}")

    category = pkg_path.parent.name
    name = pkg_path.name
    is_git = is_git_repo(pkg_path)
    repo_root = git_root(pkg_path) if is_git else pkg_path
    base_branch = args.base or (git_default_branch(repo_root) if is_git else "master")
    feature_branch = args.branch or generate_branch_name(category, name)
    original_branch = git_current_branch(repo_root) if is_git else None

    return UpdateContext(
        category=category,
        name=name,
        pkg_path=pkg_path,
        repo_root=repo_root,
        is_git=is_git,
        latest=latest,
        ebuilds=ebuilds,
        base_branch=base_branch,
        feature_branch=feature_branch,
        original_branch=original_branch,
    )


def build_update_plan(
    context: UpdateContext,
    normalized_version: str,
    *,
    keep_old: bool,
) -> UpdatePlan:
    new_filename = f"{context.latest.pn}-{normalized_version}.ebuild"
    new_path = context.pkg_path / new_filename
    repo_name = read_repo_name(context.repo_root)
    drop_ebuild = None if keep_old else select_ebuild_to_drop(context.ebuilds)
    drop_cache_path = None
    if drop_ebuild:
        drop_cache_path = metadata_cache_path(
            context.repo_root,
            context.category,
            context.name,
            drop_ebuild.pv,
        )

    commit_message = f"{context.category}/{context.name}: add {normalized_version}"
    if drop_ebuild:
        commit_message += f", drop {drop_ebuild.pv}"

    return UpdatePlan(
        context=context,
        normalized_version=normalized_version,
        new_filename=new_filename,
        new_path=new_path,
        repo_name=repo_name,
        new_cache_path=metadata_cache_path(
            context.repo_root,
            context.category,
            context.name,
            normalized_version,
        ),
        drop_ebuild=drop_ebuild,
        drop_cache_path=drop_cache_path,
        commit_message=commit_message,
    )


def render_header(log: Logger, args: argparse.Namespace, plan: UpdatePlan) -> None:
    log.banner("overlay-tools", "Update ebuild and optional PR workflow")
    log.rule("Update Ebuild")
    log.package_summary(
        plan.context.category,
        plan.context.name,
        plan.context.latest.pv,
        plan.normalized_version,
    )
    if args.pr:
        if plan.context.is_git:
            log.step("repo", f"git root {plan.context.repo_root}")
        else:
            log.warning(f"Not in a git repository: {plan.context.repo_root}")
        log.step("branch", plan.context.feature_branch)
        log.step("base", plan.context.base_branch)


def render_dry_run(log: Logger, args: argparse.Namespace, plan: UpdatePlan) -> int:
    log.step("copy", f"{plan.context.latest.path.name} → {plan.new_filename}")
    if args.my_pv:
        log.step("MY_PV", f'update to "{args.my_pv}"')

    if plan.drop_ebuild:
        log.step("drop", plan.drop_ebuild.path.name)
    else:
        log.step("drop", "none; retain existing versions")

    log.step("manifest", "skip" if args.skip_manifest else "update")
    if plan.repo_name:
        log.step("cache", "update metadata/md5-cache")
        if plan.drop_cache_path and plan.drop_cache_path.exists():
            log.step("cache-rm", str(plan.drop_cache_path.relative_to(plan.context.repo_root)))
    else:
        log.step("cache", "skip metadata/md5-cache update (repo name unavailable)")

    if args.pr:
        from overlay_tools.core.gh_utils import gh_find_open_update_pr_for_package, gh_is_available

        existing_pr = None
        if gh_is_available() and plan.context.is_git:
            try:
                existing_pr = gh_find_open_update_pr_for_package(
                    plan.context.repo_root,
                    category=plan.context.category,
                    name=plan.context.name,
                    base=plan.context.base_branch,
                )
            except Exception as exc:
                log.warning(f"Could not check for existing PRs: {exc}")

        if existing_pr:
            log.step("pr", f"reuse #{existing_pr.number} on {existing_pr.head_ref}")
        else:
            log.step("pr", f"create via {plan.context.feature_branch}")
    elif not args.skip_git and plan.context.is_git:
        log.step("commit", plan.commit_message)

    log.success("Dry run complete - no changes made")
    return 0


def prepare_pr_branch(log: Logger, plan: UpdatePlan) -> tuple[str, object | None]:
    from overlay_tools.core.gh_utils import gh_find_open_update_pr_for_package, gh_require_available

    feature_branch = plan.context.feature_branch
    existing_pr_ref = None

    try:
        gh_require_available()
        existing_pr_ref = gh_find_open_update_pr_for_package(
            plan.context.repo_root,
            category=plan.context.category,
            name=plan.context.name,
            base=plan.context.base_branch,
        )
        if existing_pr_ref:
            feature_branch = existing_pr_ref.head_ref
            log.step("pr", f"reuse #{existing_pr_ref.number} on {feature_branch}")
    except ExternalToolMissingError:
        pass
    except Exception as exc:
        log.warning(f"Could not check for existing PRs: {exc}")
        log.info("Will create new PR if none exists for this branch")

    if git_branch_exists(feature_branch, plan.context.repo_root):
        log.step("branch", f"checkout existing {feature_branch}")
        git_checkout_branch(feature_branch, plan.context.repo_root)
    elif git_branch_exists(feature_branch, plan.context.repo_root, remote=True):
        log.step("branch", f"track origin/{feature_branch}")
        git_checkout_branch(feature_branch, plan.context.repo_root, track_remote=True)
    else:
        log.step("branch", f"create {feature_branch} from {plan.context.base_branch}")
        git_checkout_branch(
            feature_branch,
            plan.context.repo_root,
            create=True,
            start_point=plan.context.base_branch,
        )

    return feature_branch, existing_pr_ref


def apply_ebuild_update(log: Logger, args: argparse.Namespace, plan: UpdatePlan) -> AppliedChanges:
    log.step("copy", f"{plan.context.latest.path.name} → {plan.new_filename}")
    shutil.copy2(plan.context.latest.path, plan.new_path)

    if args.my_pv:
        if update_ebuild_var(plan.new_path, "MY_PV", args.my_pv):
            log.success(f'Updated MY_PV="{args.my_pv}"')
        else:
            log.warning("MY_PV not found in ebuild - variable not updated")

    deleted_oldest = None
    deleted_cache_path = None
    if plan.drop_ebuild:
        log.step("drop", plan.drop_ebuild.path.name)
        plan.drop_ebuild.path.unlink()
        deleted_oldest = plan.drop_ebuild.path
        if plan.drop_cache_path and plan.drop_cache_path.exists():
            log.step("cache-rm", str(plan.drop_cache_path.relative_to(plan.context.repo_root)))
            plan.drop_cache_path.unlink()
            deleted_cache_path = plan.drop_cache_path

    return AppliedChanges(
        deleted_ebuild_path=deleted_oldest,
        deleted_cache_path=deleted_cache_path,
    )


def update_manifest_and_cache(log: Logger, args: argparse.Namespace, plan: UpdatePlan) -> RefreshedArtifacts:
    refreshed_paths: list[Path] = []

    if args.skip_manifest:
        log.step("manifest", "skip (--skip-manifest)")
    else:
        log.step("manifest", "update")
        try:
            run_ebuild_manifest(plan.new_path)
            manifest_path = plan.context.pkg_path / "Manifest"
            if not manifest_path.exists():
                raise RuntimeError(f"ebuild manifest did not create {manifest_path.name}")
            log.success("Manifest updated")
            refreshed_paths.append(manifest_path)
        except Exception as exc:
            raise RuntimeError(f"Manifest update failed for {plan.new_path.name}: {exc}") from exc

    if plan.repo_name:
        log.step("cache", f"update {plan.context.category}/{plan.context.name}")
        try:
            run_egencache_update(
                plan.context.repo_root,
                repo_name=plan.repo_name,
                atom=f"{plan.context.category}/{plan.context.name}",
            )
            if plan.new_cache_path.exists():
                log.success("metadata/md5-cache updated")
                refreshed_paths.append(plan.new_cache_path)
            else:
                rel_path = plan.new_cache_path.relative_to(plan.context.repo_root)
                raise RuntimeError(f"egencache did not create {rel_path}")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(
                "metadata/md5-cache update failed for "
                f"{plan.context.category}/{plan.context.name}: {exc}"
            ) from exc
    else:
        if args.skip_git or not plan.context.is_git:
            log.warning("Could not determine repo name from profiles/repo_name")
            log.info("Skipping metadata/md5-cache update")
            return RefreshedArtifacts(paths=tuple(refreshed_paths))
        raise RuntimeError("Could not determine repo name from profiles/repo_name")

    return RefreshedArtifacts(paths=tuple(refreshed_paths))


def collect_paths_to_stage(
    plan: UpdatePlan,
    applied_changes: AppliedChanges,
    refreshed_artifacts: RefreshedArtifacts,
) -> list[Path]:
    paths_to_add = [plan.new_path, *refreshed_artifacts.paths]
    if applied_changes.deleted_ebuild_path:
        paths_to_add.append(applied_changes.deleted_ebuild_path)
    if applied_changes.deleted_cache_path:
        paths_to_add.append(applied_changes.deleted_cache_path)
    return paths_to_add


def should_commit(log: Logger, args: argparse.Namespace) -> bool:
    if args.yes or args.pr:
        return True

    if not sys.stdin.isatty():
        log.info("Non-interactive mode detected, skipping commit (use -y to commit)")
        return False

    try:
        from rich.prompt import Confirm

        return Confirm.ask("Commit these changes?", default=False)
    except (ImportError, EOFError):
        try:
            response = input("Commit these changes? [y/N] ").strip().lower()
            return response == "y"
        except EOFError:
            return False


def commit_changes(log: Logger, plan: UpdatePlan, paths_to_add: list[Path]) -> None:
    git_add(paths_to_add, plan.context.repo_root)
    git_commit(plan.commit_message, plan.context.repo_root)
    log.success(f"Committed: {plan.commit_message}")


def create_or_update_pr(
    log: Logger,
    args: argparse.Namespace,
    plan: UpdatePlan,
    *,
    feature_branch: str,
    existing_pr_ref: object | None,
) -> int:
    from overlay_tools.core.gh_utils import gh_create_pr, gh_edit_pr, gh_require_available

    try:
        gh_require_available()
    except Exception as exc:
        log.error(str(exc))
        log.info("Commit was created but PR was not. Push manually and create PR.")
        return 1

    log.step("push", feature_branch)
    try:
        git_push(plan.context.repo_root, branch=feature_branch, set_upstream=True)
        log.success("Pushed to origin")
    except Exception as exc:
        log.error(f"Push failed: {exc}")
        return 1

    pr_title = plan.commit_message
    pr_body = generate_pr_body(
        category=plan.context.category,
        name=plan.context.name,
        old_version=plan.context.latest.pv,
        new_version=plan.normalized_version,
        my_pv=args.my_pv,
        upstream_url=args.upstream_url,
        dropped_version=plan.drop_ebuild.pv if plan.drop_ebuild else None,
    )

    if existing_pr_ref:
        log.step("pr", f"update #{existing_pr_ref.number}")
        try:
            gh_edit_pr(
                plan.context.repo_root,
                number=existing_pr_ref.number,
                title=pr_title,
                body=pr_body,
            )
            log.success(f"PR updated: {existing_pr_ref.url}")
            return 0
        except Exception as exc:
            log.warning(f"PR update failed: {exc}")
            log.info(f"Changes were pushed. Update PR manually: {existing_pr_ref.url}")
            return 0

    log.step("pr", "create")
    try:
        pr = gh_create_pr(
            plan.context.repo_root,
            title=pr_title,
            body=pr_body,
            head=feature_branch,
            base=plan.context.base_branch,
            draft=args.draft,
        )
        log.success(f"PR created: {pr.url}")
        return 0
    except Exception as exc:
        log.error(f"PR creation failed: {exc}")
        log.info(f"Branch {feature_branch} was pushed. Create PR manually.")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.pr and args.skip_git:
        parser.error("--pr cannot be used with --skip-git")
    if args.pr:
        args.yes = True

    log = Logger(verbose=True, quiet=False)
    set_logger(log)

    try:
        normalized_version = normalize_gentoo_version(args.version, lenient=args.lenient)
    except VersionError as exc:
        log.error(str(exc))
        if not args.lenient:
            log.info("Hint: Use -l/--lenient for non-standard version formats")
        return 1

    try:
        context = build_context(args, normalized_version)
    except (ValueError, ExternalToolMissingError) as exc:
        log.error(str(exc))
        return 1
    if args.pr and not context.is_git:
        log.error("--pr requires running inside a git repository")
        return 1
    if args.pr and context.feature_branch == context.base_branch:
        log.error("--branch must differ from the PR base branch")
        return 1

    plan = build_update_plan(context, normalized_version, keep_old=args.keep_old)
    if plan.context.is_git and not args.skip_git and not plan.repo_name:
        log.error("Could not determine repo name from profiles/repo_name")
        return 1
    if args.skip_manifest and plan.context.is_git and not args.skip_git:
        log.error("--skip-manifest cannot be used for git-backed commit/PR runs")
        return 1
    render_header(log, args, plan)

    if args.dry_run:
        return render_dry_run(log, args, plan)

    feature_branch = plan.context.feature_branch
    existing_pr_ref = None
    switched_branch = False

    try:
        if args.pr and plan.context.is_git:
            feature_branch, existing_pr_ref = prepare_pr_branch(log, plan)
            switched_branch = (
                plan.context.original_branch is not None
                and feature_branch != plan.context.original_branch
            )

        applied_changes = apply_ebuild_update(log, args, plan)
        refreshed_artifacts = update_manifest_and_cache(log, args, plan)

        if args.skip_git or not plan.context.is_git:
            log.rule()
            if args.skip_git:
                log.info("Git operations skipped (--skip-git)")
            else:
                log.warning(f"Not a git repository: {plan.context.pkg_path}")
                log.info("Changes were made but not committed. Run manually from a git repo.")
            log.success("Done!")
            return 0

        log.rule("Git")
        if not should_commit(log, args):
            log.info("Changes not committed")
            return 0
        paths_to_add = collect_paths_to_stage(plan, applied_changes, refreshed_artifacts)
        commit_changes(log, plan, paths_to_add)

        if not args.pr:
            log.rule()
            log.success("Done!")
            return 0

        log.rule("Pull Request")
        result = create_or_update_pr(
            log,
            args,
            plan,
            feature_branch=feature_branch,
            existing_pr_ref=existing_pr_ref,
        )
        if result == 0:
            log.rule()
            log.success("Done!")
        return result
    except Exception as exc:
        log.error(str(exc))
        return 1
    finally:
        if switched_branch and plan.context.original_branch:
            try:
                log.step("branch", f"return to {plan.context.original_branch}")
                git_checkout_branch(plan.context.original_branch, plan.context.repo_root)
            except Exception as exc:
                log.error(
                    f"Failed to return to branch {plan.context.original_branch}: {exc}"
                )


if __name__ == "__main__":
    sys.exit(main())
