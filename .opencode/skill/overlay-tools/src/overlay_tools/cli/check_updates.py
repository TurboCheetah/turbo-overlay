from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from overlay_tools.core.ebuilds import read_ebuild_vars
from overlay_tools.core.github import (
    GitHubAPIError,
    GitHubClient,
    extract_github_repo,
    extract_github_repo_from_path,
)
from overlay_tools.core.logging import Logger, set_logger
from overlay_tools.core.overlay import find_overlay_root, find_packages, get_package_latest_ebuild
from overlay_tools.core.report import PackageStatus, render_json, render_terminal_report
from overlay_tools.core.versions import compare_versions

VENDOR_URLS: dict[str, str] = {
    "hayase": "https://hayase.watch/",
    "warp": "https://www.warp.dev/changelog",
}


def get_custom_url(name: str, src_uri: str | None, homepage: str | None) -> str | None:
    if src_uri:
        if "hayase.watch" in src_uri:
            return "https://hayase.watch/"
        if "warp.dev" in src_uri:
            return "https://www.warp.dev/changelog"

    if homepage:
        if "hayase.watch" in homepage:
            return "https://hayase.watch/"
        if "warp.dev" in homepage:
            return "https://www.warp.dev/changelog"

    name_lower = name.lower()
    for vendor, url in VENDOR_URLS.items():
        if vendor in name_lower:
            return url

    return None


def check_package(
    category: str,
    name: str,
    pkg_path: Path,
    github_client: GitHubClient,
) -> PackageStatus:
    ebuild = get_package_latest_ebuild(pkg_path)

    if not ebuild:
        return PackageStatus(
            category=category,
            name=name,
            current_version="unknown",
            latest_version=None,
            github_repo=None,
            custom_url=None,
            status="error",
            error_message="No ebuild found",
        )

    current_version = ebuild.pv
    ebuild_vars = read_ebuild_vars(ebuild.path, {"SRC_URI", "HOMEPAGE", "MY_PV"})
    src_uri = ebuild_vars.get("SRC_URI")
    homepage = ebuild_vars.get("HOMEPAGE")

    github_repo = extract_github_repo(src_uri)
    if not github_repo:
        github_repo = extract_github_repo_from_path(pkg_path / "metadata.xml")

    custom_url = get_custom_url(name, src_uri, homepage)

    my_pv = ebuild_vars.get("MY_PV")

    if github_repo:
        try:
            release = github_client.get_latest_release(github_repo)
            if release:
                cmp = compare_versions(current_version, release.version)
                status = "update-available" if cmp < 0 else "up-to-date"

                return PackageStatus(
                    category=category,
                    name=name,
                    current_version=current_version,
                    latest_version=release.version,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status=status,
                    latest_url=release.url,
                    my_pv=my_pv,
                )
            elif custom_url:
                return PackageStatus(
                    category=category,
                    name=name,
                    current_version=current_version,
                    latest_version=None,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status="manual-check",
                    my_pv=my_pv,
                )
            else:
                return PackageStatus(
                    category=category,
                    name=name,
                    current_version=current_version,
                    latest_version=None,
                    github_repo=github_repo,
                    custom_url=None,
                    status="error",
                    error_message="No releases or tags found",
                    my_pv=my_pv,
                )
        except GitHubAPIError as e:
            if custom_url:
                return PackageStatus(
                    category=category,
                    name=name,
                    current_version=current_version,
                    latest_version=None,
                    github_repo=github_repo,
                    custom_url=custom_url,
                    status="manual-check",
                    my_pv=my_pv,
                )
            return PackageStatus(
                category=category,
                name=name,
                current_version=current_version,
                latest_version=None,
                github_repo=github_repo,
                custom_url=None,
                status="error",
                error_message=str(e),
                my_pv=my_pv,
            )

    if custom_url:
        return PackageStatus(
            category=category,
            name=name,
            current_version=current_version,
            latest_version=None,
            github_repo=None,
            custom_url=custom_url,
            status="manual-check",
            my_pv=my_pv,
        )

    return PackageStatus(
        category=category,
        name=name,
        current_version=current_version,
        latest_version=None,
        github_repo=None,
        custom_url=None,
        status="unknown",
        my_pv=my_pv,
    )


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
    cache_dir = overlay_root / ".opencode" / "skill" / "overlay-tools" / ".cache"
    github_client = GitHubClient(token=github_token, cache_dir=cache_dir)

    packages = find_packages(overlay_root)

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
        render_terminal_report(results, verbose=args.verbose)

    if any(p.status == "error" for p in results):
        return 1
    elif any(p.status == "update-available" for p in results):
        return 0
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
