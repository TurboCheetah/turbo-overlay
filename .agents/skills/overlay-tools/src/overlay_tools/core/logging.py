from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    from rich.console import Console as RichConsole
    from rich.progress import Progress as RichProgress


def _get_rich() -> tuple[Any, ...] | None:
    try:
        from rich.box import ROUNDED
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        from rich.table import Table
        from rich.text import Text
        from rich.theme import Theme

        return (
            Console,
            Panel,
            Progress,
            Table,
            Text,
            Theme,
            ROUNDED,
            SpinnerColumn,
            TextColumn,
            BarColumn,
            TaskProgressColumn,
            TimeElapsedColumn,
        )
    except ImportError:
        return None


THEME_DICT = {
    "info": "cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "dim": "dim white",
    "package": "bold magenta",
    "version": "bold cyan",
    "version.old": "dim",
    "version.new": "bold yellow",
    "accent": "bold bright_cyan",
    "muted": "bright_black",
}

PLAIN_ICONS = {
    "info": "[INFO]",
    "success": "[OK]",
    "warning": "[WARN]",
    "error": "[ERROR]",
    "debug": "[DEBUG]",
}

RICH_ICONS = {
    "info": "ℹ",
    "success": "✓",
    "warning": "⚠",
    "error": "✗",
}
GENTOO_ICON = ""


class Logger:
    def __init__(self, verbose: bool = False, quiet: bool = False):
        self.verbose = verbose
        self.quiet = quiet

        rich_imports = _get_rich()
        if rich_imports:
            Console, _, _, _, _, Theme, *_ = rich_imports
            theme = Theme(THEME_DICT)
            self.console: Any = Console(theme=theme, stderr=True)
            self.stdout: Any = Console(theme=theme)
            self._rich = rich_imports
        else:
            self.console = None
            self.stdout = None
            self._rich = None

    def banner(self, title: str, subtitle: str | None = None) -> None:
        if self.quiet:
            return
        if self.console and self._rich:
            _, Panel, _, _, Text, _, box_style, *_ = self._rich
            header = Text()
            header.append(f"{GENTOO_ICON} ", style="accent")
            header.append(title, style="package")
            if subtitle:
                header.append(f"\n{subtitle}", style="dim")
            self.console.print(Panel(header, box=box_style, border_style="accent", padding=(0, 2)))
        else:
            print(title, file=sys.stderr)
            if subtitle:
                print(subtitle, file=sys.stderr)

    def info(self, message: str, **kwargs: Any) -> None:
        self._print("info", message, **kwargs)

    def success(self, message: str, **kwargs: Any) -> None:
        self._print("success", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._print("warning", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._print("error", message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        if not self.verbose:
            return
        if self.console:
            self.console.print(f"[dim]   → {message}[/dim]", **kwargs)
        else:
            print(f"{PLAIN_ICONS['debug']} {message}", file=sys.stderr)

    def package(self, category: str, name: str, action: str = "") -> None:
        if self.quiet:
            return
        pkg = f"{category}/{name}"
        if self.console:
            if action:
                self.console.print(f"[package]{pkg}[/package] [dim]{action}[/dim]")
            else:
                self.console.print(f"[package]{pkg}[/package]")
        else:
            if action:
                print(f"{pkg}: {action}", file=sys.stderr)
            else:
                print(pkg, file=sys.stderr)

    def package_summary(self, category: str, name: str, old: str, new: str) -> None:
        if self.quiet:
            return
        if self.console and self._rich:
            _, _, _, Table, _, _, box_style, *_ = self._rich
            table = Table(box=box_style, border_style="muted", show_header=False, pad_edge=False)
            table.add_column(style="dim", no_wrap=True)
            table.add_column()
            table.add_row("Package", f"[package]{category}/{name}[/package]")
            table.add_row("Change", f"[version.old]{old}[/version.old] → [version.new]{new}[/version.new]")
            self.console.print(table)
        else:
            print(f"{category}/{name}", file=sys.stderr)
            print(f"  {old} -> {new}", file=sys.stderr)

    def version_change(self, old: str, new: str) -> None:
        if self.quiet:
            return
        if self.console:
            self.console.print(
                f"     [version.old]{old}[/version.old] → [version.new]{new}[/version.new]"
            )
        else:
            print(f"  {old} -> {new}", file=sys.stderr)

    def step(self, label: str, message: str, *, level: str = "info") -> None:
        if self.quiet:
            return
        if self.console:
            self.console.print(f"[accent]{label:>10}[/accent] [dim]•[/dim] {message}")
        else:
            self._print(level, f"{label}: {message}")

    def rule(self, title: str = "") -> None:
        if self.quiet:
            return
        if self.console:
            self.console.rule(title, style="muted")
        else:
            if title:
                print(f"--- {title} ---", file=sys.stderr)
            else:
                print("-" * 40, file=sys.stderr)

    def _print(self, level: str, message: str, **kwargs: Any) -> None:
        if self.quiet:
            return
        if self.console:
            icon = RICH_ICONS[level]
            self.console.print(f"[{level}]{icon}[/{level}]  {message}", **kwargs)
        else:
            print(f"{PLAIN_ICONS[level]} {message}", file=sys.stderr)

    @contextmanager
    def progress(self, description: str, total: int) -> Generator[ProgressContext, None, None]:
        ctx = ProgressContext(self, description, total)
        try:
            ctx._start()
            yield ctx
        finally:
            ctx._stop()


class ProgressContext:
    def __init__(self, logger: Logger, description: str, total: int):
        self.logger = logger
        self.description = description
        self.total = total
        self._progress: Any = None
        self._task_id: Any = None
        self._current = 0

    def _start(self) -> None:
        if self.logger._rich and not self.logger.quiet:
            (
                _,
                _,
                Progress,
                _,
                _,
                _,
                _,
                SpinnerColumn,
                TextColumn,
                BarColumn,
                TaskProgressColumn,
                TimeElapsedColumn,
            ) = self.logger._rich
            self._progress = Progress(
                SpinnerColumn(style="accent"),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=30),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=self.logger.console,
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(f"[accent]{self.description}", total=self.total)

    def _stop(self) -> None:
        if self._progress:
            self._progress.stop()

    def advance(self, description: str | None = None) -> None:
        self._current += 1
        if self._progress and self._task_id is not None:
            if description:
                self._progress.update(
                    self._task_id,
                    description=f"[accent]{description}",
                    advance=1,
                )
            else:
                self._progress.advance(self._task_id)
        elif not self.logger.quiet and not self.logger._rich:
            pct = int(self._current / self.total * 100) if self.total > 0 else 0
            msg = description or self.description
            print(f"\r[{pct:3d}%] {msg}", end="", file=sys.stderr, flush=True)
            if self._current == self.total:
                print(file=sys.stderr)


_default_logger: Logger | None = None


def get_logger() -> Logger:
    global _default_logger
    if _default_logger is None:
        _default_logger = Logger()
    return _default_logger


def set_logger(logger: Logger) -> None:
    global _default_logger
    _default_logger = logger
