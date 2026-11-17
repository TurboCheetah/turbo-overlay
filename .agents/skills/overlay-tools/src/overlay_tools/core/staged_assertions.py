"""Declarative install-image checks. Also runs standalone under stage3's Python."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import deque
from pathlib import Path
from typing import cast

STAGED_PATH = re.compile(r"^[A-Za-z0-9+_.-]+(/[A-Za-z0-9+_.-]+)*$")


def validate_path(path: str) -> None:
    if not STAGED_PATH.fullmatch(path) or any(p in {".", ".."} for p in path.split("/")):
        raise ValueError(f"staged path must be relative: {path}")


def parse_specs(
    args: list[str], *, ebuild: str | None = None, registry: Path | None = None
) -> list[dict[str, str]]:
    checks = []
    pending = iter(args)
    for arg in pending:
        extra = {}
        if arg == "--package-checks":
            if ebuild is None or registry is None:
                raise ValueError("package checks require an exact ebuild and registry")
            checks.extend(package_checks(ebuild, registry))
            continue
        if arg == "--assertions-json":
            if len(args) != 2:
                raise ValueError("cannot combine --assertions-json with assertion specs")
            try:
                checks.extend(validate_checks(load_json(next(pending))))
            except StopIteration as exc:
                raise ValueError(f"{arg} requires a value") from exc
            continue
        if arg in {
            "--expect",
            "--expect-executable",
            "--expect-type",
            "--expect-mode",
            "--expect-link-target",
            "--expect-resolved-link",
        }:
            try:
                path = next(pending)
            except StopIteration as exc:
                raise ValueError(f"{arg} requires a value") from exc
            kind = "exists" if arg == "--expect" else arg.removeprefix("--expect-")
            if kind == "type":
                expected, separator, path = path.partition(":")
                if not separator or expected not in {"file", "directory", "symlink"}:
                    raise ValueError("--expect-type requires file/directory/symlink:PATH")
                extra["type"] = expected
            elif kind == "mode":
                expected, separator, path = path.partition(":")
                if not separator or not re.fullmatch(r"[0-7]{3,4}", expected):
                    raise ValueError("--expect-mode requires octal MODE:PATH (0000..7777)")
                extra["mode"] = expected
            elif kind == "link-target":
                path, separator, target = path.partition("=")
                if not separator or not target or "\x00" in target:
                    raise ValueError("--expect-link-target requires PATH=TARGET (nonempty text)")
                extra["target"] = target
        elif arg.startswith("--"):
            raise ValueError(f"unknown assertion option: {arg}")
        else:
            path, kind = arg, "exists"
        validate_path(path)
        checks.append({"kind": kind, "path": path, **extra})
    return validate_checks(checks)


def validate_checks(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TypeError("assertions must be a list of builtin assertion objects")
    checks: list[dict[str, str]] = []
    fields: dict[str, set[str]] = {
        "exists": set(),
        "executable": set(),
        "resolved-link": set(),
        "type": {"type"},
        "mode": {"mode"},
        "link-target": {"target"},
    }
    for raw in value:
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
        ):
            raise ValueError("assertion objects require string keys and values")
        item = cast(dict[str, str], raw)
        kind = item.get("kind", "")
        if kind not in fields or set(item) != {"kind", "path"} | fields[kind]:
            raise ValueError(f"unknown assertion kind or fields: {kind}")
        validate_path(item["path"])
        if kind == "type" and item["type"] not in {"file", "directory", "symlink"}:
            raise ValueError("assertion type must be file, directory or symlink")
        if kind == "mode" and not re.fullmatch(r"[0-7]{3,4}", item["mode"]):
            raise ValueError("assertion mode must be octal (0000..7777)")
        if kind == "link-target" and (not item["target"] or "\x00" in item["target"]):
            raise ValueError("assertion link target must be nonempty text without NUL")
        checks.append(item)
    return checks


def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(text: str) -> object:
    return json.loads(text, object_pairs_hook=unique_keys)


def package_checks(ebuild: str, registry: Path) -> list[dict[str, str]]:
    try:
        raw = load_json(registry.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"version", "packages"}:
            raise ValueError("expected version and packages fields only")
        data = cast(dict[str, object], raw)
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("registry version must be integer 1")
        packages = data["packages"]
        if not isinstance(packages, dict):
            raise TypeError("packages must map exact atoms to assertion lists")
        category_name = r"[A-Za-z0-9_][A-Za-z0-9+_.-]*"
        package_name = r"[A-Za-z0-9_][A-Za-z0-9+_-]*"
        version = r"[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?"
        atom_pattern = re.compile(rf"^={category_name}/(?P<package>{package_name})-{version}$")
        version_suffix = re.compile(rf"-{version}$")
        validated: dict[str, list[dict[str, str]]] = {}
        for atom, values in packages.items():
            # After splitting the final version/revision, PN must not end in a version (PMS 3.1.2).
            if (
                not isinstance(atom, str)
                or (match := atom_pattern.fullmatch(atom)) is None
                or version_suffix.search(match["package"])
            ):
                raise ValueError(f"invalid exact package atom: {atom}")
            if any(part in {".", ".."} for part in atom[1:].split("/")):
                raise ValueError(f"invalid exact package atom: {atom}")
            checks = validate_checks(values)
            if not checks:
                raise ValueError(f"empty assertion list: {atom}")
            validated[atom] = checks
        category = ebuild.split("/")[0]
        atom = f"={category}/{Path(ebuild).name.removesuffix('.ebuild')}"
        if atom not in validated:
            raise ValueError(f"no package checks registered for exact atom {atom}")
        return validated[atom]
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"package checks registry {registry}: {exc}") from exc


def image_path(root: Path, path: str, *, follow_final: bool = True) -> Path:
    """Resolve every link manually, interpreting / as image root, never OS root."""
    pending = deque(path.split("/"))
    parts: list[str] = []
    links = 0
    while pending:
        part = pending.popleft()
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValueError("symlink escapes image root")
            parts.pop()
            continue
        current = root.joinpath(*parts, part)
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) and (pending or follow_final):
            links += 1
            if links > 40:
                raise ValueError("symlink cycle or chain exceeds 40 links")
            target = os.readlink(current)
            if target.startswith("/"):
                parts.clear()
            pending.extendleft(reversed(target.split("/")))
        else:
            if pending and not stat.S_ISDIR(mode):
                raise ValueError("non-directory in staged path")
            parts.append(part)
    return root.joinpath(*parts)


def check_image(root: Path, checks: list[dict[str, str]]) -> int:
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("install image must be a real directory")
    except (OSError, ValueError) as exc:
        print(f"Assertion failed: install image: {exc}", file=sys.stderr)
        return 1
    for check in checks:
        kind, path = check["kind"], check["path"]
        try:
            if kind == "exists":
                # Preserve legacy presence semantics, including dangling final links.
                current = root
                for component in path.split("/")[:-1]:
                    current /= component
                    if not stat.S_ISDIR(current.lstat().st_mode):
                        raise ValueError("legacy path parent must be a real directory")
                (current / path.split("/")[-1]).lstat()
            elif kind == "type":
                mode = image_path(root, path, follow_final=False).lstat().st_mode
                predicate = {
                    "file": stat.S_ISREG,
                    "directory": stat.S_ISDIR,
                    "symlink": stat.S_ISLNK,
                }[check["type"]]
                if not predicate(mode):
                    raise ValueError(f"expected {check['type']}")
            elif kind in {"link-target", "resolved-link"}:
                current = image_path(root, path, follow_final=False)
                if not stat.S_ISLNK(current.lstat().st_mode):
                    raise ValueError("expected symlink")
                if kind == "link-target":
                    if os.readlink(current) != check["target"]:
                        raise ValueError(f"expected link target {check['target']!r}")
                else:
                    image_path(root, path).lstat()
            elif kind == "mode":
                mode = image_path(root, path).lstat().st_mode
                if stat.S_IMODE(mode) != int(check["mode"], 8):
                    raise ValueError(f"expected mode {check['mode']}, got {stat.S_IMODE(mode):04o}")
            else:
                mode = image_path(root, path).lstat().st_mode
                if not stat.S_ISREG(mode) or not mode & 0o111:
                    raise ValueError("expected a regular file with execute bits")
        except (OSError, ValueError) as exc:
            print(f"Assertion failed: {kind} {path}: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {kind} {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "check"))
    parser.add_argument("--image", type=Path)
    parser.add_argument("--assertions-json", action="append")
    parser.add_argument("--ebuild")
    parser.add_argument("--registry", type=Path)
    args, specs = parser.parse_known_args(argv)
    try:
        specs = specs[1:] if specs[:1] == ["--"] else specs
        if args.assertions_json is not None:
            if len(args.assertions_json) != 1:
                raise ValueError("--assertions-json may be supplied only once")
            args.assertions_json = args.assertions_json[0]
        if args.assertions_json is not None and specs:
            raise ValueError("cannot combine --assertions-json with assertion specs")
        checks = (
            validate_checks(load_json(args.assertions_json))
            if args.assertions_json is not None
            else parse_specs(specs, ebuild=args.ebuild, registry=args.registry)
        )
        if args.action == "validate":
            print(json.dumps(checks))
            return 0
        if args.image is None:
            raise ValueError("check requires --image")
        return check_image(args.image, checks)
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
