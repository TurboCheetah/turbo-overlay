from __future__ import annotations

import argparse
import os
import sys
from functools import cmp_to_key
from pathlib import Path

from overlay_tools.core.ebuilds import EbuildName, find_ebuilds, read_ebuild_vars
from overlay_tools.core.github import (
    GitHubAPIError,
    GitHubClient,
    extract_github_repo,
    extract_github_repo_from_path,
)
from overlay_tools.core.logging import Logger, set_logger
from overlay_tools.core.overlay import find_overlay_root, find_packages
from overlay_tools.core.package_sources import get_custom_latest_release, get_custom_url
from overlay_tools.core.report import PackageStatus, build_status, render_json, render_terminal_report
from overlay_tools.core.versions import compare_versions, upstream_to_gentoo


CHANNEL_SUFFIXES = ("stable", "preview", "dev")


def _derive_channel(my_pv: str | None) -> str | None:
    """Derive release channel from MY_PV value (e.g., '.stable_' -> 'stable')."""
    if not my_pv:
        return None
    for ch in CHANNEL_SUFFIXES:
        if f".{ch}_" in my_pv:
            return ch
    return None


def _group_ebuilds_by_channel(ebuilds: list[EbuildName]) -> dict[str | None, EbuildName]:
    """Group ebuilds by channel, returning the latest ebuild per channel."""
    groups: dict[str | None, list[EbuildName]] = {}
    for eb in ebuilds:
        vars_ = read_ebuild_vars(eb.path, {"MY_PV"})
        ch = _derive_channel(vars_.get("MY_PV"))
        groups.setdefault(ch, []).append(eb)

    return {
        ch: max(ebs, key=cmp_to_key(lambda a, b: compare_versions(a.pv, b.pv)))
        for ch, ebs in groups.items()
    }


def check_channel_ebuild(
    category: str,
    name: str,
    ebuild: EbuildName,
    pkg_path: Path,
    github_client: GitHubClient,
) -> PackageStatus:
    current_version = ebuild.pv
    ebuild_vars = read_ebuild_vars(ebuild.path, {"SRC_URI", "HOMEPAGE", "MY_PV"})
    src_uri = ebuild_vars.get("SRC_URI")
    homepage = ebuild_vars.get("HOMEPAGE")

    github_repo = extract_github_repo(src_uri)
    if not github_repo:
        github_repo = extract_github_repo_from_path(pkg_path / "metadata.xml")

    custom_url = get_custom_url(name, src_uri, homepage)
    my_pv = ebuild_vars.get("MY_PV")
    channel = _derive_channel(my_pv)

    custom_release = get_custom_latest_release(custom_url)
    if custom_release:
        cmp = compare_versions(current_version, custom_release.version)
        status = "update-available" if cmp < 0 else "up-to-date"
        gentoo_version = upstream_to_gentoo(custom_release.version)

        return build_status(
            category=category,
            name=name,
            current_version=current_version,
            latest_version=custom_release.version,
            gentoo_version=gentoo_version,
            github_repo=github_repo,
            custom_url=custom_url,
            status=status,
            latest_url=custom_release.url,
            my_pv=my_pv,
        )

    if github_repo:
        try:
            release = github_client.get_latest_release(github_repo, channel=channel)
            if release:
                cmp = compare_versions(current_version, release.version)
                status = "update-available" if cmp < 0 else "up-to-date"
                gentoo_version = upstream_to_gentoo(release.version)

                return build_status(
                    category=category,
                    name=name,
                    current_version=current_version,
                    latest_version=release.version,
                    gentoo_version=gentoo_version,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status=status,
                    latest_url=release.url,
                    my_pv=my_pv,
                )
            if custom_url:
                return build_status(
                    category=category,
                    name=name,
                    current_version=current_version,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status="manual-check",
                    my_pv=my_pv,
                )
            return build_status(
                category=category,
                name=name,
                current_version=current_version,
                github_repo=github_repo,
                status="error",
                error_message="No releases or tags found",
                my_pv=my_pv,
            )
        except GitHubAPIError as e:
            if custom_url:
                return build_status(
                    category=category,
                    name=name,
                    current_version=current_version,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status="manual-check",
                    my_pv=my_pv,
                )
            return build_status(
                category=category,
                name=name,
                current_version=current_version,
                github_repo=github_repo,
                status="error",
                error_message=str(e),
                my_pv=my_pv,
            )

    if custom_url:
        return build_status(
            category=category,
            name=name,
            current_version=current_version,
            custom_url=custom_url,
            status="manual-check",
            my_pv=my_pv,
        )

    return build_status(
        category=category,
        name=name,
        current_version=current_version,
        status="unknown",
        my_pv=my_pv,
    )


def check_package(
    category: str,
    name: str,
    pkg_path: Path,
    github_client: GitHubClient,
) -> PackageStatus:
    ebuilds = find_ebuilds(pkg_path)
    if not ebuilds:
        return build_status(
            category=category,
            name=name,
            current_version="unknown",
            status="error",
            error_message="No ebuilds found",
        )

    channels = _group_ebuilds_by_channel(ebuilds)
    if not channels:
        return build_status(
            category=category,
            name=name,
            current_version="unknown",
            status="error",
            error_message="No channel groups found",
        )

    # Pick the channel whose latest ebuild is the overall highest version.
    # This is the ebuild Portage would select by default — check *that*
    # channel against upstream.
    best_channel = max(channels.items(), key=cmp_to_key(lambda a, b: compare_versions(a[1].pv, b[1].pv)))  # type: ignore[arg-type]
    _, best_ebuild = best_channel
    return check_channel_ebuild(category, name, best_ebuild, pkg_path, github_client)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-updates",
        description="Check turbo-overlay packages for upstream updates",
    )
    parser.add_argument(
        "--package",
        "-p",
        metavar="CATEGORY/NAME",
        help="Check specific package only",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--overlay-path",
        metavar="PATH",
        default=".",
        help="Path to overlay (default: current directory)",
    )

    args = parser.parse_args(argv)

    log = Logger(verbose=args.verbose, quiet=args.json)
    set_logger(log)

    overlay_path = Path(args.overlay_path).resolve()
    overlay_root = find_overlay_root(overlay_path)

    if not overlay_root:
        log.error(f"Not a valid Gentoo overlay: {overlay_path}")
        log.info("Run from overlay root or use --overlay-path")
        return 1

    github_token = os.environ.get("GITHUB_TOKEN")
    cache_dir = overlay_root / ".agents" / "skills" / "overlay-tools" / ".cache"
    github_client = GitHubClient(token=github_token, cache_dir=cache_dir)

    packages = find_packages(overlay_root)
    log.banner("overlay-tools", "Check upstream package updates")

    if args.package:
        packages = [p for p in packages if p.atom == args.package]
        if not packages:
            log.warning(f"Package '{args.package}' not found")

    results: list[PackageStatus] = []

    with log.progress("Checking packages", len(packages)) as progress:
        for pkg in packages:
            progress.advance(pkg.atom)
            log.debug(f"Checking {pkg.atom}")
            status = check_package(pkg.category, pkg.name, pkg.path, github_client)
            results.append(status)

    if args.json:
        print(render_json(results))
    else:
        render_terminal_report(results)

    if any(p.status == "error" for p in results):
        return 1
    elif any(p.status == "update-available" for p in results):
        return 0
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
