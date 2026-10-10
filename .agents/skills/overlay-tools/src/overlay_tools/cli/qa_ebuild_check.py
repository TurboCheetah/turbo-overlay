"""Informational filename hook and authoritative changed-package CI adapter."""

import sys

from overlay_tools.cli.qa_ebuild import Parser, print_report
from overlay_tools.core.qa import (
    QAInputError,
    Report,
    run_local,
    select_all,
    select_changed,
    select_paths,
    validate_root,
)


def informational_requested(arguments: list[str]) -> bool:
    """Recover only an explicit flag on invalid input, not a value or filename.

    Reserve the next token for each value-taking option even if argparse rejects
    that value. The missing-environment Python bootstrap uses the same conservative
    rules; neither may infer a zero-exit opt-in from an ambiguous value.
    """
    needs_value = False
    for argument in arguments:
        if argument == "--":
            break
        if needs_value:
            needs_value = False
        elif argument in {"--overlay-path", "--changed-since"}:
            needs_value = True
        elif argument == "--informational":
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    informational = False
    report = Report()
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--overlay-path", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--informational", action="store_true")
    mode.add_argument("--changed-since", metavar="REF")
    mode.add_argument(
        "--initial", action="store_true", help="First push: check all current packages"
    )
    parser.add_argument("paths", nargs="*")
    try:
        try:
            args = parser.parse_args(arguments)
        except QAInputError:
            informational = informational_requested(arguments)
            raise
        informational = args.informational
        if args.paths and not args.informational:
            raise QAInputError("CI change selection cannot be combined with filename targets")
        root = validate_root(args.overlay_path)
        if args.changed_since is not None:
            packages = select_changed(root, args.changed_since, report)
            if not packages:
                print("not applicable: no package targets changed")
                for item in report.skipped:
                    print(f"skipped: {item['check']}: {item.get('path', '')}: {item['reason']}")
                print("No packages examined; not package PASS.")
                return 0
        elif args.initial:
            print("First push has no base commit; checking all current packages.")
            packages = select_all(root)
        else:
            packages = select_paths(root, args.paths, report)
        if not packages:
            raise QAInputError("Empty selection; no current package targets")
        run_local(root, packages, report)
    except (QAInputError, OSError, UnicodeError) as exc:
        report.inconclusive = True
        report.add("input_environment", getattr(exc, "path", ""), str(exc))
    if informational:
        print("informational pre-commit QA; wrapper exit 0 is not package PASS")
    print_report(report)
    if informational:
        print(f"authoritative QA exit: {report.exit_code}; informational wrapper exit: 0")
        return 0
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
