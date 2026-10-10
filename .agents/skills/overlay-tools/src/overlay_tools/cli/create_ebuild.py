"""Public CLI for opt-in binary ebuild scaffolding."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from overlay_tools.core.create import build_plan, report, write_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("atom", help="Exact unversioned category/package")
    parser.add_argument("--overlay-path", type=Path, required=True, help="Existing overlay root")
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--template",
        choices=[
            "binary-direct",
            "binary-deb",
            "binary-appimage-intact",
        ],
        required=True,
    )
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--homepage", required=True)
    parser.add_argument("--maintainer-email", required=True)
    parser.add_argument("--maintainer-name")
    parser.add_argument("--binary-name", help="Defaults to package without a trailing -bin")
    parser.add_argument(
        "--keywords", default="~amd64", help="Space-separated keywords; default ~amd64"
    )
    parser.add_argument("--eapi", choices=["8"], default="8")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only, also the default")
    mode.add_argument("--write", action="store_true", help="Create new package; never overwrite")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
            root=args.overlay_path,
            atom=args.atom,
            version=args.version,
            template=args.template,
            upstream_url=args.upstream_url,
            license_name=args.license,
            description=args.description,
            homepage=args.homepage,
            maintainer_email=args.maintainer_email,
            maintainer_name=args.maintainer_name,
            binary_name=args.binary_name,
            keywords=args.keywords,
        )
        if args.write:
            write_plan(plan)
        print(report(plan, written=args.write))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
