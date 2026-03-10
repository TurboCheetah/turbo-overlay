from __future__ import annotations

import json
from dataclasses import asdict, dataclass


STATUS_ORDER = {
    "update-available": 0,
    "manual-check": 1,
    "error": 2,
    "unknown": 3,
    "up-to-date": 4,
}
GENTOO_ICON = ""


@dataclass
class PackageStatus:
    category: str
    name: str
    current_version: str
    latest_version: str | None
    github_repo: str | None
    custom_url: str | None
    status: str
    error_message: str | None = None
    latest_url: str | None = None
    my_pv: str | None = None

    @property
    def atom(self) -> str:
        return f"{self.category}/{self.name}"


@dataclass(frozen=True)
class StatusSummary:
    updates: int = 0
    up_to_date: int = 0
    manual: int = 0
    errors: int = 0

    @property
    def checked(self) -> int:
        return self.up_to_date + self.updates


def build_status(
    category: str,
    name: str,
    current_version: str,
    status: str,
    *,
    latest_version: str | None = None,
    github_repo: str | None = None,
    custom_url: str | None = None,
    error_message: str | None = None,
    latest_url: str | None = None,
    my_pv: str | None = None,
) -> PackageStatus:
    return PackageStatus(
        category=category,
        name=name,
        current_version=current_version,
        latest_version=latest_version,
        github_repo=github_repo,
        custom_url=custom_url,
        status=status,
        error_message=error_message,
        latest_url=latest_url,
        my_pv=my_pv,
    )


def render_json(packages: list[PackageStatus]) -> str:
    return json.dumps([asdict(p) for p in packages], indent=2)


def sort_packages(packages: list[PackageStatus]) -> list[PackageStatus]:
    return sorted(packages, key=lambda p: (STATUS_ORDER.get(p.status, 99), p.category, p.name))


def summarize_packages(packages: list[PackageStatus]) -> StatusSummary:
    counts = {"updates": 0, "up_to_date": 0, "manual": 0, "errors": 0}

    for package in packages:
        if package.status == "update-available":
            counts["updates"] += 1
        elif package.status == "up-to-date":
            counts["up_to_date"] += 1
        elif package.status == "manual-check":
            counts["manual"] += 1
        else:
            counts["errors"] += 1

    return StatusSummary(**counts)


def render_terminal_report(packages: list[PackageStatus]) -> None:
    try:
        _render_rich(packages)
    except ImportError:
        _render_plain(packages)


def _render_rich(packages: list[PackageStatus]) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme

    theme = Theme(
        {
            "header": "bold magenta",
            "pkg": "bold white",
            "version.current": "dim",
            "version.new": "bold yellow",
            "status.update": "bold yellow",
            "status.ok": "green",
            "status.manual": "yellow",
            "status.error": "red",
            "source": "dim cyan",
        }
    )

    packages_sorted = sort_packages(packages)
    summary = summarize_packages(packages_sorted)

    console = Console(theme=theme)
    console.print()

    header = Text()
    header.append(f"{GENTOO_ICON} ", style="bold cyan")
    header.append("turbo-overlay", style="bold magenta")
    header.append(" Update Check", style="bold white")
    console.print(Panel(header, box=box.DOUBLE_EDGE, border_style="magenta", padding=(0, 2)))
    console.print()

    table = Table(
        box=box.ROUNDED,
        border_style="bright_black",
        show_header=True,
        header_style="bold cyan",
        row_styles=["", "dim"],
    )
    table.add_column("", justify="center", width=2)
    table.add_column("Package", style="bold")
    table.add_column("Current", style="dim", justify="right")
    table.add_column("", justify="center", width=2)
    table.add_column("Latest", justify="left")
    table.add_column("Source", style="dim")

    for package in packages_sorted:
        icon, latest, source = _format_row(package)
        table.add_row(
            icon,
            Text(package.atom, style="bold"),
            Text(package.current_version, style="dim"),
            Text("→", style="dim"),
            latest,
            source,
        )

    console.print(table)
    console.print()

    stats = Table.grid(padding=(0, 3))
    stats.add_column(justify="right")
    stats.add_column(justify="left")
    stats.add_row(Text(str(summary.checked), style="bold cyan"), Text("packages checked", style="dim"))

    if summary.updates > 0:
        stats.add_row(
            Text(str(summary.updates), style="bold yellow"),
            Text("updates available 🚀", style="yellow"),
        )

    if summary.manual > 0:
        stats.add_row(
            Text(str(summary.manual), style="yellow"),
            Text("need manual check", style="dim yellow"),
        )

    if summary.errors > 0:
        stats.add_row(Text(str(summary.errors), style="red"), Text("errors", style="dim red"))

    console.print(
        Panel(
            stats,
            title="[bold white]Summary[/]",
            border_style="bright_black",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()

    if summary.updates > 0:
        tip = Text()
        tip.append("💡 ", style="dim")
        tip.append("Run ", style="dim")
        tip.append("/update-ebuild", style="bold cyan")
        tip.append(" to bump versions", style="dim")
        console.print(tip)
        console.print()


def _render_plain(packages: list[PackageStatus]) -> None:
    packages_sorted = sort_packages(packages)
    summary = summarize_packages(packages_sorted)

    print()
    print("turbo-overlay Update Check")
    print("=" * 50)
    print()

    for package in packages_sorted:
        if package.status == "update-available":
            print(f"🚀 {package.atom}")
            print(f"   {package.current_version} -> {package.latest_version}")
            print(f"   GitHub: {package.github_repo}")
        elif package.status == "up-to-date":
            print(f"✓  {package.atom}")
            print(f"   {package.current_version} (up to date)")
        elif package.status == "manual-check":
            print(f"👀 {package.atom}")
            print(f"   {package.current_version} -> check manually")
            print(f"   {package.custom_url or 'Unknown source'}")
        elif package.status == "error":
            print(f"✗  {package.atom}")
            print(f"   {package.current_version}")
            print(f"   Error: {package.error_message or 'Unknown error'}")
        else:
            print(f"?  {package.atom}")
            print(f"   {package.current_version} (unknown source)")
        print()

    print("-" * 50)
    print(f"{summary.checked} packages checked")
    if summary.updates:
        print(f"{summary.updates} updates available")
    if summary.manual:
        print(f"{summary.manual} need manual check")
    if summary.errors:
        print(f"{summary.errors} errors")
    print()


def _format_row(package: PackageStatus) -> tuple["Text", "Text", "Text"]:
    from rich.text import Text

    if package.status == "update-available":
        return (
            Text("🚀"),
            Text(package.latest_version or "?", style="bold yellow"),
            Text(package.github_repo or "", style="cyan"),
        )
    if package.status == "up-to-date":
        return (
            Text("✓", style="green"),
            Text(package.latest_version or "?", style="green"),
            Text(package.github_repo or "", style="dim"),
        )
    if package.status == "manual-check":
        return (
            Text("👀"),
            Text("check", style="yellow"),
            Text(package.custom_url or "", style="yellow dim"),
        )
    if package.status == "error":
        return (
            Text("✗", style="red"),
            Text("error", style="red"),
            Text(package.error_message or "", style="red dim"),
        )
    return Text("?", style="dim"), Text("?", style="dim"), Text("")
