from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    from rich.console import Console as RichConsole
    from rich.progress import Progress as RichProgress


def _get_rich() -> tuple[Any, ...] | None:
    try:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        from rich.theme import Theme

        return (
            Console,
            Progress,
            Theme,
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
}


class Logger:
    def __init__(self, verbose: bool = False, quiet: bool = False):
        self.verbose = verbose
        self.quiet = quiet

        rich_imports = _get_rich()
        if rich_imports:
            Console, _, Theme, *_ = rich_imports
            theme = Theme(THEME_DICT)
            self.console: Any = Console(theme=theme, stderr=True)
            self.stdout: Any = Console(theme=theme)
            self._rich = rich_imports
        else:
            self.console = None
            self.stdout = None
            self._rich = None

    def info(self, message: str, **kwargs: Any) -> None:
        if self.quiet:
            return
        if self.console:
            self.console.print(f"[info]ℹ[/info]  {message}", **kwargs)
        else:
            print(f"[INFO] {message}", file=sys.stderr)

    def success(self, message: str, **kwargs: Any) -> None:
        if self.quiet:
            return
        if self.console:
            self.console.print(f"[success]✓[/success]  {message}", **kwargs)
        else:
            print(f"[OK] {message}", file=sys.stderr)

    def warning(self, message: str, **kwargs: Any) -> None:
        if self.console:
            self.console.print(f"[warning]⚠[/warning]  {message}", **kwargs)
        else:
            print(f"[WARN] {message}", file=sys.stderr)

    def error(self, message: str, **kwargs: Any) -> None:
        if self.console:
            self.console.print(f"[error]✗[/error]  {message}", **kwargs)
        else:
            print(f"[ERROR] {message}", file=sys.stderr)

    def debug(self, message: str, **kwargs: Any) -> None:
        if not self.verbose:
            return
        if self.console:
            self.console.print(f"[dim]   → {message}[/dim]", **kwargs)
        else:
            print(f"[DEBUG] {message}", file=sys.stderr)

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

    def version_change(self, old: str, new: str) -> None:
        if self.quiet:
            return
        if self.console:
            self.console.print(
                f"     [version.old]{old}[/version.old] → [version.new]{new}[/version.new]"
            )
        else:
            print(f"  {old} -> {new}", file=sys.stderr)

    def rule(self, title: str = "") -> None:
        if self.quiet:
            return
        if self.console:
            self.console.rule(title, style="dim")
        else:
            if title:
                print(f"--- {title} ---", file=sys.stderr)
            else:
                print("-" * 40, file=sys.stderr)

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
                Progress,
                _,
                SpinnerColumn,
                TextColumn,
                BarColumn,
                TaskProgressColumn,
                TimeElapsedColumn,
            ) = self.logger._rich
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=30),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=self.logger.console,
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(f"[cyan]{self.description}", total=self.total)

    def _stop(self) -> None:
        if self._progress:
            self._progress.stop()

    def advance(self, description: str | None = None) -> None:
        self._current += 1
        if self._progress and self._task_id is not None:
            if description:
                self._progress.update(
                    self._task_id,
                    description=f"[cyan]{description}",
                    advance=1,
                )
            else:
                self._progress.advance(self._task_id)
        elif not self.logger.quiet and not self.logger._rich:
            pct = int(self._current / self.total * 100)
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
