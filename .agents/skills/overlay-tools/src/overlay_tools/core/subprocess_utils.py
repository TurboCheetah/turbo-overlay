from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from overlay_tools.core.errors import ExternalToolMissingError


def which(program: str) -> Path | None:
    path = shutil.which(program)
    return Path(path) if path else None


def require_tool(program: str, install_hint: str | None = None) -> Path:
    path = which(program)
    if not path:
        raise ExternalToolMissingError(program, install_hint)
    return path


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
    )


def detect_privilege_cmd() -> str | None:
    if which("doas") and Path("/etc/doas.conf").exists():
        return "doas"
    if which("sudo"):
        return "sudo"
    return None


def run_ebuild_manifest(ebuild_path: Path) -> subprocess.CompletedProcess[str]:
    priv_cmd = detect_privilege_cmd()
    cmd = ["ebuild", str(ebuild_path), "manifest"]
    if priv_cmd:
        cmd = [priv_cmd] + cmd
    return run(cmd, cwd=ebuild_path.parent, check=True)
