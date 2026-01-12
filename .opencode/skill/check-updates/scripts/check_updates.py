#!/usr/bin/env python3
"""
check_updates.py - Scan turbo-overlay for available upstream updates

Intelligently scans Gentoo overlay packages, extracts upstream source info,
and checks for newer versions using GitHub API or flags for manual review.

Usage:
    python check_updates.py [--package CATEGORY/NAME] [--json] [--verbose]

Exit Codes:
    0 - Updates available
    1 - Errors occurred
    2 - All packages up-to-date
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any, TYPE_CHECKING
from xml.etree import ElementTree as ET

try:
    import requests
    from packaging import version
except ImportError:
    print("Error: Missing dependencies. Install with:", file=sys.stderr)
    print("  pip install requests packaging rich", file=sys.stderr)
    sys.exit(1)

if TYPE_CHECKING:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
    )
    from rich.text import Text
    from rich import box as rich_box

RICH_AVAILABLE = False
console: Any = None
Panel: Any = None
Table: Any = None
Progress: Any = None
SpinnerColumn: Any = None
TextColumn: Any = None
BarColumn: Any = None
TaskProgressColumn: Any = None
Text: Any = None
box: Any = None

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
    )
    from rich.text import Text
    from rich import box

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    pass


@dataclass
class PackageInfo:
    category: str
    name: str
    current_version: str
    ebuild_path: str
    github_repo: Optional[str] = None
    custom_url: Optional[str] = None
    homepage: Optional[str] = None
    checkable: str = "unknown"
    latest_version: Optional[str] = None
    status: str = "unknown"
    error_message: Optional[str] = None


class OverlayScanner:
    SKIP_DIRS = {"metadata", "profiles", "licenses", "deprecated", ".git", ".opencode"}

    def __init__(self, overlay_path: Path):
        self.overlay_path = overlay_path

    def find_packages(self) -> List[PackageInfo]:
        packages = []

        for category_dir in self.overlay_path.iterdir():
            if not category_dir.is_dir():
                continue
            if category_dir.name.startswith(".") or category_dir.name in self.SKIP_DIRS:
                continue

            for package_dir in category_dir.iterdir():
                if not package_dir.is_dir():
                    continue
                if package_dir.name.startswith("."):
                    continue

                ebuilds = list(package_dir.glob("*.ebuild"))
                if not ebuilds:
                    continue

                versioned_ebuilds = [e for e in ebuilds if "9999" not in e.stem]
                if not versioned_ebuilds:
                    continue

                latest_ebuild = max(
                    versioned_ebuilds, key=lambda e: self._parse_ebuild_version(e.stem)
                )

                pkg_info = self._parse_ebuild(
                    category_dir.name, package_dir.name, latest_ebuild
                )
                if pkg_info:
                    packages.append(pkg_info)

        return packages

    def _parse_ebuild_version(self, ebuild_stem: str) -> Tuple:
        match = re.search(r"-(\d+.*?)(?:-r\d+)?$", ebuild_stem)
        if match:
            ver_str = match.group(1)
            try:
                normalized = (
                    ver_str.replace("_p", ".")
                    .replace("_pre", "-pre")
                    .replace("_alpha", "-alpha")
                    .replace("_beta", "-beta")
                    .replace("_rc", "-rc")
                )
                return (version.parse(normalized), ver_str)
            except Exception:
                return (version.parse("0"), ver_str)
        return (version.parse("0"), "0")

    def _parse_ebuild(
        self, category: str, name: str, ebuild_path: Path
    ) -> Optional[PackageInfo]:
        ebuild_stem = ebuild_path.stem
        ver_match = re.search(r"-(\d+.*?)(?:-r\d+)?$", ebuild_stem)
        if not ver_match:
            return None
        current_version = ver_match.group(1)

        pkg = PackageInfo(
            category=category,
            name=name,
            current_version=current_version,
            ebuild_path=str(ebuild_path),
        )

        try:
            content = ebuild_path.read_text()

            homepage_match = re.search(r'^HOMEPAGE="([^"]+)"', content, re.MULTILINE)
            if homepage_match:
                pkg.homepage = homepage_match.group(1).split()[0]

            src_uri_match = re.search(
                r'^SRC_URI="([^"]+)"', content, re.MULTILINE | re.DOTALL
            )
            if src_uri_match:
                src_uri = src_uri_match.group(1)

                github_match = re.search(r"github\.com/([^/]+/[^/]+)", src_uri)
                if github_match:
                    repo = github_match.group(1)
                    repo = re.sub(r"\.git$", "", repo)
                    repo = re.sub(r"/releases.*", "", repo)
                    repo = re.sub(r"/archive.*", "", repo)
                    repo = re.sub(r"/raw.*", "", repo)
                    pkg.github_repo = repo
                    pkg.checkable = "yes"

                elif "hayase.watch" in src_uri or "hayase.watch" in (
                    pkg.homepage or ""
                ):
                    pkg.custom_url = "https://hayase.watch/"
                    pkg.checkable = "manual"

                elif "warp.dev" in src_uri or "warp.dev" in (pkg.homepage or ""):
                    pkg.custom_url = "https://www.warp.dev/changelog"
                    pkg.checkable = "manual"

        except Exception as e:
            pkg.error_message = f"Failed to parse ebuild: {e}"
            pkg.checkable = "no"
            return pkg

        if not pkg.github_repo:
            metadata_path = ebuild_path.parent / "metadata.xml"
            if metadata_path.exists():
                try:
                    tree = ET.parse(metadata_path)
                    root = tree.getroot()
                    for remote_id in root.findall('.//remote-id[@type="github"]'):
                        if remote_id.text:
                            pkg.github_repo = remote_id.text.strip()
                            pkg.checkable = "yes"
                            break
                except Exception:
                    pass

        if pkg.custom_url is None:
            pkg.custom_url = self._get_vendor_fallback_url(name)

        return pkg

    def _get_vendor_fallback_url(self, name: str) -> Optional[str]:
        VENDOR_URLS = {
            "hayase": "https://hayase.watch/",
            "warp": "https://www.warp.dev/changelog",
        }
        name_lower = name.lower()
        for vendor, url in VENDOR_URLS.items():
            if vendor in name_lower:
                return url
        return None


class GitHubChecker:
    API_BASE = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.token = token
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "turbo-overlay-check-updates/1.0"
        if token:
            self.session.headers["Authorization"] = f"token {token}"

        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def check_latest_release(self, repo: str) -> Optional[str]:
        cache_key = repo.replace("/", "_")
        cache_file = self.cache_dir / f"{cache_key}.json" if self.cache_dir else None

        if cache_file and cache_file.exists():
            cache_age = time.time() - cache_file.stat().st_mtime
            if cache_age < 1800:
                try:
                    cached = json.loads(cache_file.read_text())
                    return cached.get("version")
                except Exception:
                    pass

        url = f"{self.API_BASE}/repos/{repo}/releases/latest"
        try:
            response = self.session.get(url, timeout=10)

            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining", "0")
                if remaining == "0":
                    reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                    wait_minutes = max(0, (reset_time - time.time()) / 60)
                    raise Exception(
                        f"GitHub API rate limit exceeded. Resets in {wait_minutes:.0f} minutes. Set GITHUB_TOKEN for higher limits."
                    )

            if response.status_code == 404:
                return self._check_latest_tag(repo)

            response.raise_for_status()
            data = response.json()

            tag = data.get("tag_name", "")
            version_str = self._normalize_tag(tag)

            if cache_file and version_str:
                cache_file.write_text(json.dumps({"version": version_str, "tag": tag}))

            return version_str

        except requests.RequestException as e:
            raise Exception(f"GitHub API error for {repo}: {e}")

    def _check_latest_tag(self, repo: str) -> Optional[str]:
        url = f"{self.API_BASE}/repos/{repo}/tags"
        try:
            response = self.session.get(url, timeout=10, params={"per_page": 1})
            response.raise_for_status()
            tags = response.json()
            if tags:
                return self._normalize_tag(tags[0].get("name", ""))
        except Exception:
            pass
        return None

    def _normalize_tag(self, tag: str) -> str:
        version_str = re.sub(r"^[vV]", "", tag)
        version_str = re.sub(r"^release-", "", version_str)
        return version_str.strip()

    def get_rate_limit_status(self) -> Dict:
        try:
            response = self.session.get(f"{self.API_BASE}/rate_limit", timeout=5)
            response.raise_for_status()
            data = response.json()
            return data.get("rate", {})
        except Exception:
            return {}


class VersionComparator:
    @staticmethod
    def normalize_gentoo_version(ver: str) -> str:
        ver = re.sub(r"_p(\d+)$", ".post\\1", ver)
        ver = re.sub(r"_pre(\d*)$", ".dev\\1", ver)
        ver = re.sub(r"_alpha(\d*)$", "a\\1", ver)
        ver = re.sub(r"_beta(\d*)$", "b\\1", ver)
        ver = re.sub(r"_rc(\d*)$", "rc\\1", ver)
        ver = re.sub(r"-r\d+$", "", ver)
        return ver

    @staticmethod
    def compare(current: str, latest: str) -> int:
        try:
            current_norm = VersionComparator.normalize_gentoo_version(current)
            latest_norm = VersionComparator.normalize_gentoo_version(latest)

            v_current = version.parse(current_norm)
            v_latest = version.parse(latest_norm)

            if v_latest > v_current:
                return 1
            elif v_latest < v_current:
                return -1
            else:
                return 0
        except Exception:
            if latest != current:
                return 1 if latest > current else -1
            return 0


class UpdateChecker:
    def __init__(
        self,
        overlay_path: Path,
        github_token: Optional[str] = None,
        verbose: bool = False,
    ):
        self.overlay_path = overlay_path
        self.verbose = verbose
        self.scanner = OverlayScanner(overlay_path)

        cache_dir = overlay_path / ".opencode" / "skill" / "check-updates" / ".cache"
        self.github_checker = GitHubChecker(token=github_token, cache_dir=cache_dir)

    def log(self, message: str, style: str = "dim"):
        if self.verbose:
            if RICH_AVAILABLE and console:
                console.print(f"  {message}", style=style)
            else:
                print(f"[INFO] {message}", file=sys.stderr)

    def check_updates(self, package_filter: Optional[str] = None) -> List[PackageInfo]:
        packages = self.scanner.find_packages()

        if package_filter:
            packages = [
                p for p in packages if f"{p.category}/{p.name}" == package_filter
            ]
            if not packages:
                if RICH_AVAILABLE and console:
                    console.print(
                        f"[yellow]⚠ Package '{package_filter}' not found[/yellow]"
                    )
                else:
                    print(
                        f"Warning: Package '{package_filter}' not found",
                        file=sys.stderr,
                    )

        if RICH_AVAILABLE and console:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(
                    "[cyan]Checking packages...", total=len(packages)
                )

                for pkg in packages:
                    progress.update(
                        task, description=f"[cyan]Checking [bold]{pkg.name}[/bold]..."
                    )
                    self._check_package(pkg)
                    progress.advance(task)
        else:
            for pkg in packages:
                self.log(f"Checking {pkg.category}/{pkg.name}...")
                self._check_package(pkg)

        return packages

    def _check_package(self, pkg: PackageInfo):
        if pkg.checkable == "yes" and pkg.github_repo:
            try:
                latest = self.github_checker.check_latest_release(pkg.github_repo)
                pkg.latest_version = latest

                if latest:
                    cmp_result = VersionComparator.compare(pkg.current_version, latest)
                    if cmp_result > 0:
                        pkg.status = "update-available"
                    elif cmp_result < 0:
                        pkg.status = "up-to-date"
                    else:
                        pkg.status = "up-to-date"
                else:
                    if pkg.custom_url:
                        pkg.status = "manual-check"
                    else:
                        pkg.status = "error"
                        pkg.error_message = "No releases or tags found"

            except Exception as e:
                if pkg.custom_url:
                    pkg.status = "manual-check"
                else:
                    pkg.status = "error"
                    pkg.error_message = str(e)

        elif pkg.checkable == "manual":
            pkg.status = "manual-check"
        else:
            pkg.status = "unknown"

    def print_report(self, packages: List[PackageInfo]):
        if not RICH_AVAILABLE or not console:
            self._print_report_plain(packages)
            return

        console.print()

        title = Text("turbo-overlay", style="bold magenta")
        title.append(" Update Check", style="bold white")
        console.print(Panel(title, box=box.DOUBLE_EDGE, border_style="magenta"))
        console.print()

        status_order = {
            "update-available": 0,
            "manual-check": 1,
            "error": 2,
            "unknown": 3,
            "up-to-date": 4,
        }
        packages_sorted = sorted(
            packages, key=lambda p: (status_order.get(p.status, 99), p.category, p.name)
        )

        updates_available = 0
        up_to_date = 0
        manual_checks = 0
        errors = 0

        table = Table(
            box=box.ROUNDED,
            border_style="dim",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Status", justify="center", width=3)
        table.add_column("Package", style="bold")
        table.add_column("Current", style="dim")
        table.add_column("Latest", style="green")
        table.add_column("Source")

        for pkg in packages_sorted:
            full_name = f"{pkg.category}/{pkg.name}"

            if pkg.status == "update-available":
                status_icon = "🚀"
                latest_display = Text(pkg.latest_version or "?", style="bold yellow")
                source = Text(f"GitHub: {pkg.github_repo}", style="dim cyan")
                updates_available += 1

            elif pkg.status == "up-to-date":
                status_icon = "✅"
                latest_display = Text(pkg.latest_version or "?", style="green")
                source = Text(f"GitHub: {pkg.github_repo}", style="dim cyan")
                up_to_date += 1

            elif pkg.status == "manual-check":
                status_icon = "👀"
                latest_display = Text("manual", style="dim yellow")
                source = Text(
                    pkg.custom_url or pkg.homepage or "unknown", style="dim yellow"
                )
                manual_checks += 1

            elif pkg.status == "error":
                status_icon = "❌"
                latest_display = Text("error", style="red")
                source = Text(pkg.error_message or "Unknown error", style="dim red")
                errors += 1

            else:
                status_icon = "❓"
                latest_display = Text("unknown", style="dim")
                source = Text("Unknown source", style="dim")
                errors += 1

            table.add_row(
                status_icon, full_name, pkg.current_version, latest_display, source
            )

        console.print(table)
        console.print()

        summary_table = Table(box=box.SIMPLE, show_header=False, border_style="dim")
        summary_table.add_column("Metric", style="dim")
        summary_table.add_column("Value", justify="right")

        summary_table.add_row(
            "Packages checked", f"[bold]{up_to_date + updates_available}[/bold]"
        )

        if updates_available > 0:
            summary_table.add_row(
                "Updates available",
                f"[bold yellow]{updates_available}[/bold yellow] 🚀",
            )
        else:
            summary_table.add_row(
                "Updates available", f"[dim]{updates_available}[/dim]"
            )

        summary_table.add_row(
            "Manual check needed",
            f"[yellow]{manual_checks}[/yellow]"
            if manual_checks
            else f"[dim]{manual_checks}[/dim]",
        )
        summary_table.add_row(
            "Errors", f"[red]{errors}[/red]" if errors else f"[dim]{errors}[/dim]"
        )

        console.print(
            Panel(
                summary_table,
                title="[bold]Summary[/bold]",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        console.print()

        if self.verbose:
            rate_limit = self.github_checker.get_rate_limit_status()
            if rate_limit:
                remaining = rate_limit.get("remaining", "?")
                limit = rate_limit.get("limit", "?")
                pct = (
                    (int(remaining) / int(limit) * 100)
                    if str(remaining).isdigit() and str(limit).isdigit()
                    else 0
                )

                rate_style = "green" if pct > 50 else "yellow" if pct > 20 else "red"
                console.print(
                    f"[dim]GitHub API:[/dim] [{rate_style}]{remaining}/{limit}[/{rate_style}] [dim]requests remaining[/dim]"
                )
                console.print()

        if updates_available > 0:
            console.print(
                "[dim]💡 Tip: Use[/dim] [bold cyan]/update-ebuild[/bold cyan] [dim]skill to bump versions[/dim]"
            )
            console.print()

    def _print_report_plain(self, packages: List[PackageInfo]):
        print()
        print("turbo-overlay Update Check")
        print("=" * 50)
        print()

        updates_available = 0
        up_to_date = 0
        manual_checks = 0
        errors = 0

        status_order = {
            "update-available": 0,
            "manual-check": 1,
            "error": 2,
            "unknown": 3,
            "up-to-date": 4,
        }
        packages_sorted = sorted(
            packages, key=lambda p: (status_order.get(p.status, 99), p.category, p.name)
        )

        for pkg in packages_sorted:
            full_name = f"{pkg.category}/{pkg.name}"

            if pkg.status == "update-available":
                print(f"✓ {full_name}")
                print(f"  Current: {pkg.current_version}")
                print(f"  Latest:  {pkg.latest_version} (GitHub: {pkg.github_repo})")
                print(f"  Status:  UPDATE AVAILABLE")
                print()
                updates_available += 1

            elif pkg.status == "up-to-date":
                print(f"✓ {full_name}")
                print(f"  Current: {pkg.current_version}")
                print(f"  Latest:  {pkg.latest_version} (GitHub: {pkg.github_repo})")
                print(f"  Status:  UP TO DATE")
                print()
                up_to_date += 1

            elif pkg.status == "manual-check":
                print(f"⚠ {full_name}")
                print(f"  Current: {pkg.current_version}")
                print(f"  Source:  {pkg.custom_url or pkg.homepage or 'Unknown'}")
                print(f"  Status:  MANUAL CHECK REQUIRED")
                print()
                manual_checks += 1

            elif pkg.status == "error":
                print(f"✗ {full_name}")
                print(f"  Current: {pkg.current_version}")
                print(f"  Status:  ERROR")
                print(f"  Error:   {pkg.error_message}")
                print()
                errors += 1

            else:
                print(f"? {full_name}")
                print(f"  Current: {pkg.current_version}")
                print(f"  Status:  UNKNOWN SOURCE")
                print()
                errors += 1

        print("-" * 50)
        print("Summary:")
        print(f"  {up_to_date + updates_available} packages checked automatically")
        print(f"  {updates_available} update(s) available")
        print(f"  {manual_checks} require manual check")
        print(f"  {errors} error(s)")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Check turbo-overlay packages for upstream updates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit Codes:
  0 - Updates available
  1 - Errors occurred  
  2 - All packages up-to-date

Examples:
  %(prog)s                           Check all packages
  %(prog)s --package net-im/goofcord Check specific package
  %(prog)s --json                    Output JSON for scripting
  GITHUB_TOKEN=ghp_xxx %(prog)s      Use token for higher rate limits
""",
    )
    parser.add_argument(
        "--package", "-p", metavar="CATEGORY/NAME", help="Check specific package only"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed progress"
    )
    parser.add_argument(
        "--overlay-path",
        metavar="PATH",
        default=".",
        help="Path to overlay (default: current directory)",
    )

    args = parser.parse_args()

    overlay_path = Path(args.overlay_path).resolve()

    if not (overlay_path / "profiles" / "repo_name").exists():
        for parent in overlay_path.parents:
            if (parent / "profiles" / "repo_name").exists():
                overlay_path = parent
                break
        else:
            if ".opencode" in str(overlay_path):
                overlay_path = overlay_path.parent
                while ".opencode" in overlay_path.name:
                    overlay_path = overlay_path.parent

    if not (overlay_path / "profiles" / "repo_name").exists():
        if RICH_AVAILABLE and console:
            console.print(
                f"[red]Error:[/red] Not a valid Gentoo overlay: {overlay_path}"
            )
            console.print("[dim]Run from overlay root or use --overlay-path[/dim]")
        else:
            print(f"Error: Not a valid Gentoo overlay: {overlay_path}", file=sys.stderr)
            print("Run from overlay root or use --overlay-path", file=sys.stderr)
        sys.exit(1)

    github_token = os.environ.get("GITHUB_TOKEN")

    checker = UpdateChecker(
        overlay_path, github_token=github_token, verbose=args.verbose
    )
    packages = checker.check_updates(package_filter=args.package)

    if args.json:
        output = [asdict(p) for p in packages]
        print(json.dumps(output, indent=2))
    else:
        checker.print_report(packages)

    if any(p.status == "error" for p in packages):
        sys.exit(1)
    elif any(p.status == "update-available" for p in packages):
        sys.exit(0)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
