"""Public read-only checkout information command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from overlay_tools.core.context import SUMMARY_OUTPUT_BYTES, generate_context
from overlay_tools.core.errors import OverlayToolsError


def render_text(context: dict[str, Any]) -> str:
    lines = ["Overlay context"]

    def append(label: str, value: Any, indent: int = 0) -> None:
        prefix = "  " * indent + label.replace("_", " ")
        if isinstance(value, dict) and {"items", "total", "truncated"} <= value.keys():
            lines.append(f"{prefix}: {value['total']} total, {value['truncated']} truncated")
            for item in value["items"]:
                lines.append("  " * (indent + 1) + json.dumps(item, ensure_ascii=True))
        elif isinstance(value, dict):
            lines.append(f"{prefix}:")
            for key, child in value.items():
                append(key, child, indent + 1)
        elif isinstance(value, list):
            lines.append(f"{prefix}:")
            for item in value:
                lines.append("  " * (indent + 1) + json.dumps(item, ensure_ascii=True))
        else:
            lines.append(f"{prefix}: {json.dumps(value, ensure_ascii=True)}")

    for key, value in context.items():
        append(key, value)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overlay-info", description=__doc__)
    parser.add_argument(
        "--overlay-path", metavar="PATH", default=".", help="Checkout or subdirectory"
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    parser.add_argument(
        "--full", action="store_true", help="Include complete lists and configuration"
    )
    args = parser.parse_args(argv)
    try:
        context = generate_context(Path(args.overlay_path).resolve(), full=args.full)
        output = (
            json.dumps(context, indent=2, ensure_ascii=True) if args.json else render_text(context)
        ) + "\n"
        if not args.full and len(output.encode("utf-8")) > SUMMARY_OUTPUT_BYTES:
            raise ValueError(
                f"Summary exceeds the {SUMMARY_OUTPUT_BYTES}-byte output budget; use --full"
            )
    except (OSError, UnicodeError, ValueError, RuntimeError, OverlayToolsError) as exc:
        print(f"overlay-info: error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
