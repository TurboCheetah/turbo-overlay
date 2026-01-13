from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


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


def render_json(packages: list[PackageStatus]) -> str:
    return json.dumps([asdict(p) for p in packages], indent=2)


def render_terminal_report(packages: list[PackageStatus], verbose: bool = False) -> None:
    try:
        _render_rich(packages, verbose)
    except ImportError:
        _render_plain(packages)


def _render_rich(packages: list[PackageStatus], verbose: bool) -> None:
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

    console = Console(theme=theme)
    console.print()

    header = Text()
    header.append("󰣇 ", style="bold cyan")
    header.append("turbo-overlay", style="bold magenta")
    header.append(" Update Check", style="bold white")
    console.print(Panel(header, box=box.DOUBLE_EDGE, border_style="magenta", padding=(0, 2)))
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

    counts: dict[str, int] = {"updates": 0, "up_to_date": 0, "manual": 0, "errors": 0}

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

    for pkg in packages_sorted:
        arrow = Text("→", style="dim")

        if pkg.status == "update-available":
            icon = Text("🚀")
            latest = Text(pkg.latest_version or "?", style="bold yellow")
            source = Text(pkg.github_repo or "", style="cyan")
            counts["updates"] += 1
        elif pkg.status == "up-to-date":
            icon = Text("✓", style="green")
            latest = Text(pkg.latest_version or "?", style="green")
            source = Text(pkg.github_repo or "", style="dim")
            counts["up_to_date"] += 1
        elif pkg.status == "manual-check":
            icon = Text("👀")
            latest = Text("check", style="yellow")
            source = Text(pkg.custom_url or "", style="yellow dim")
            counts["manual"] += 1
        elif pkg.status == "error":
            icon = Text("✗", style="red")
            latest = Text("error", style="red")
            source = Text(pkg.error_message or "", style="red dim")
            counts["errors"] += 1
        else:
            icon = Text("?", style="dim")
            latest = Text("?", style="dim")
            source = Text("")
            counts["errors"] += 1

        table.add_row(
            icon,
            Text(pkg.atom, style="bold"),
            Text(pkg.current_version, style="dim"),
            arrow,
            latest,
            source,
        )

    console.print(table)
    console.print()

    stats = Table.grid(padding=(0, 3))
    stats.add_column(justify="right")
    stats.add_column(justify="left")

    checked = counts["up_to_date"] + counts["updates"]
    stats.add_row(
        Text(str(checked), style="bold cyan"),
        Text("packages checked", style="dim"),
    )

    if counts["updates"] > 0:
        stats.add_row(
            Text(str(counts["updates"]), style="bold yellow"),
            Text("updates available 🚀", style="yellow"),
        )

    if counts["manual"] > 0:
        stats.add_row(
            Text(str(counts["manual"]), style="yellow"),
            Text("need manual check", style="dim yellow"),
        )

    if counts["errors"] > 0:
        stats.add_row(
            Text(str(counts["errors"]), style="red"),
            Text("errors", style="dim red"),
        )

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

    if counts["updates"] > 0:
        tip = Text()
        tip.append("💡 ", style="dim")
        tip.append("Run ", style="dim")
        tip.append("/update-ebuild", style="bold cyan")
        tip.append(" to bump versions", style="dim")
        console.print(tip)
        console.print()


def _render_plain(packages: list[PackageStatus]) -> None:
    print()
    print("turbo-overlay Update Check")
    print("=" * 50)
    print()

    counts: dict[str, int] = {"updates": 0, "up_to_date": 0, "manual": 0, "errors": 0}

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
        if pkg.status == "update-available":
            print(f"🚀 {pkg.atom}")
            print(f"   {pkg.current_version} -> {pkg.latest_version}")
            print(f"   GitHub: {pkg.github_repo}")
            counts["updates"] += 1
        elif pkg.status == "up-to-date":
            print(f"✓  {pkg.atom}")
            print(f"   {pkg.current_version} (up to date)")
            counts["up_to_date"] += 1
        elif pkg.status == "manual-check":
            print(f"👀 {pkg.atom}")
            print(f"   {pkg.current_version} -> check manually")
            print(f"   {pkg.custom_url or 'Unknown source'}")
            counts["manual"] += 1
        elif pkg.status == "error":
            print(f"✗  {pkg.atom}")
            print(f"   {pkg.current_version}")
            print(f"   Error: {pkg.error_message}")
            counts["errors"] += 1
        else:
            print(f"?  {pkg.atom}")
            print(f"   {pkg.current_version} (unknown source)")
            counts["errors"] += 1
        print()

    print("-" * 50)
    checked = counts["up_to_date"] + counts["updates"]
    print(f"{checked} packages checked")
    if counts["updates"]:
        print(f"{counts['updates']} updates available")
    if counts["manual"]:
        print(f"{counts['manual']} need manual check")
    if counts["errors"]:
        print(f"{counts['errors']} errors")
    print()
