from __future__ import annotations

import json
import re
from pathlib import Path

from overlay_tools.core.errors import OverlayToolsError

EXACT_ATOM_RE = re.compile(r"[A-Za-z0-9+_][A-Za-z0-9+_.-]*/[A-Za-z0-9+_][A-Za-z0-9+_-]*")
VERSION_SUFFIX_RE = re.compile(
    r"-[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?$"
)


class UpdatePolicyError(OverlayToolsError):
    """Repository update policy is unreadable or invalid."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate atom {key!r}")
        result[key] = value
    return result


def load_update_exclusions(overlay_root: Path) -> dict[str, str]:
    """Read explicit update exclusions, independently of Portage package masks."""
    path = overlay_root / "metadata" / "update-exclusions.json"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise UpdatePolicyError(f"Invalid update policy {path}: {exc}") from exc

    try:
        exclusions = json.loads(content, object_pairs_hook=_unique_object)
    except ValueError as exc:
        raise UpdatePolicyError(f"Invalid update policy {path}: {exc}") from exc
    if not isinstance(exclusions, dict):
        raise UpdatePolicyError(f"Invalid update policy {path}: expected an atom-to-reason object")

    validated: dict[str, str] = {}
    for atom, reason in exclusions.items():
        if not EXACT_ATOM_RE.fullmatch(atom) or VERSION_SUFFIX_RE.search(atom.split("/")[-1]):
            raise UpdatePolicyError(
                f"Invalid update policy {path}: {atom!r} must be an exact category/package atom"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise UpdatePolicyError(
                f"Invalid update policy {path}: reason for {atom!r} must be a non-empty string"
            )
        validated[atom] = reason
    return validated
