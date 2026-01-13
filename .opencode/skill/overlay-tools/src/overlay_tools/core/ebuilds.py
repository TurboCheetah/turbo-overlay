from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from overlay_tools.core.errors import EbuildParseError

EBUILD_FILENAME_RE = re.compile(r"^(?P<pn>.+)-(?P<pv>\d+.*)\.ebuild$")

VAR_PATTERN = re.compile(
    r'^(?P<name>[A-Z_][A-Z0-9_]*)=(?P<quote>["\']?)(?P<value>.*?)(?P=quote)\s*$',
    re.MULTILINE,
)


class EbuildName(NamedTuple):
    pn: str
    pv: str
    path: Path


def parse_ebuild_filename(path: Path | str) -> EbuildName:
    path = Path(path)
    match = EBUILD_FILENAME_RE.match(path.name)
    if not match:
        raise EbuildParseError(f"Invalid ebuild filename: {path.name}")

    return EbuildName(pn=match.group("pn"), pv=match.group("pv"), path=path)


def read_ebuild_vars(path: Path, keys: set[str] | None = None) -> dict[str, str]:
    content = path.read_text()
    result: dict[str, str] = {}

    for match in VAR_PATTERN.finditer(content):
        name = match.group("name")
        if keys is None or name in keys:
            result[name] = match.group("value")

    return result


def update_ebuild_var(path: Path, key: str, value: str) -> bool:
    content = path.read_text()

    pattern = re.compile(rf'^({re.escape(key)}=)(["\']?).*?\2\s*$', re.MULTILINE)

    if not pattern.search(content):
        return False

    needs_quote = " " in value or '"' in value or "'" in value or "$" in value
    if needs_quote:
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        replacement = rf'\1"{escaped_value}"'
    else:
        replacement = rf'\1"{value}"'

    new_content = pattern.sub(replacement, content)
    path.write_text(new_content)
    return True


def find_ebuilds(pkg_dir: Path) -> list[EbuildName]:
    ebuilds = []
    for ebuild_path in pkg_dir.glob("*.ebuild"):
        try:
            ebuilds.append(parse_ebuild_filename(ebuild_path))
        except EbuildParseError:
            continue
    return ebuilds


def select_latest_ebuild(ebuilds: list[EbuildName], exclude_live: bool = True) -> EbuildName | None:
    from overlay_tools.core.versions import compare_versions

    candidates = ebuilds
    if exclude_live:
        candidates = [e for e in ebuilds if "9999" not in e.pv]

    if not candidates:
        return None

    return max(candidates, key=lambda e: (compare_versions(e.pv, "0"), e.pv))
