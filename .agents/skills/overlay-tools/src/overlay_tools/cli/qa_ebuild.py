"""Public CLI for local read-only QA."""

import argparse
import json
import sys
from typing import NoReturn

from overlay_tools.core.qa import (
    QAInputError,
    Report,
    run_local,
    run_pkgcheck,
    select_all,
    select_changed,
    select_explicit,
    validate_root,
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise QAInputError(message)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    report = Report()
    parser = Parser(
        description="Read-only local QA; does not verify remote DIST bytes", allow_abbrev=False
    )
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--overlay-path", required=True)
    parser.add_argument("--json", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--changed-since", metavar="REF")
    parser.add_argument("--pkgcheck", action="store_true")
    try:
        args = parser.parse_args(arguments)
        if args.targets and (args.all or args.changed_since is not None):
            raise QAInputError("Targets cannot be combined with --all or --changed-since")
        root = validate_root(args.overlay_path)
        if args.changed_since is not None:
            packages = select_changed(root, args.changed_since, report)
        else:
            packages = select_all(root) if args.all else select_explicit(root, args.targets)
        if not packages:
            raise QAInputError("Empty selection; provide targets, --all or --changed-since REF")
        run_local(root, packages, report)
        if args.pkgcheck:
            run_pkgcheck(root, packages, report)
    except (QAInputError, OSError, UnicodeError) as exc:
        report.inconclusive = True
        report.add("input_environment", getattr(exc, "path", ""), str(exc))
    print_report(report, json_output="--json" in arguments)
    return report.exit_code


def print_report(report: Report, *, json_output: bool = False) -> None:
    """Render the same authoritative report for direct and controller callers."""
    if json_output:
        print(json.dumps(report.to_dict(), sort_keys=True))
    else:
        print(f"{report.to_dict()['status']}: examined {len(report.packages)} package(s)")
        if report.selected_packages:
            print(
                f"Selected {len(report.selected_packages)} package(s); coverage: {report.coverage}"
            )
            for atom, coverage in report.package_coverage.items():
                print(f"package coverage: {atom}: {coverage}")
        for finding in report.findings:
            print(f"{finding.severity}: {finding.check}: {finding.path}: {finding.message}")
        for item in report.skipped:
            print(f"skipped: {item['check']}: {item.get('path', '')}: {item['reason']}")
        print("Remote DIST bytes and Bash-expanded metadata are unchecked.")


if __name__ == "__main__":
    sys.exit(main())
