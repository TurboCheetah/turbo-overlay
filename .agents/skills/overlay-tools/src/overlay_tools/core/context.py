"""Read checkout facts without executing overlay code or maintaining a cache."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from overlay_tools.core.ebuilds import parse_ebuild_filename
from overlay_tools.core.errors import EbuildParseError
from overlay_tools.core.overlay import SKIP_DIRS, PackageRef
from overlay_tools.core.update_policy import EXACT_ATOM_RE, VERSION_SUFFIX_RE, UpdatePolicyError

TOOLS_PATH = ".agents/skills/overlay-tools"
SUMMARY_ITEMS = 10
SUMMARY_REASON_CHARACTERS = 240
SUMMARY_STRING_CHARACTERS = 240
SUMMARY_OUTPUT_BYTES = 131_072


def bound_strings(context: dict[str, Any]) -> dict[str, Any]:
    """Keep exact prefixes, with codepoint counts and unambiguous JSON pointers."""
    truncations: list[dict[str, Any]] = []

    def bound(value: Any, pointer: str) -> Any:
        if isinstance(value, str):
            limit = SUMMARY_STRING_CHARACTERS
            if len(value) > limit:
                truncations.append(
                    {"path": pointer, "characters": len(value), "truncated": len(value) - limit}
                )
            return value[:limit]
        if isinstance(value, list):
            return [bound(item, f"{pointer}/{index}") for index, item in enumerate(value)]
        if isinstance(value, dict):
            result = {
                key: bound(child, f"{pointer}/{key.replace('~', '~0').replace('/', '~1')}")
                for key, child in value.items()
            }
            if {"reason", "reason_characters", "reason_truncated"} <= result.keys():
                result["reason_truncated"] = result["reason_characters"] - len(result["reason"])
            return result
        return value

    result = bound(context, "")
    result["string_truncations"] = truncations
    return result


def section(items: list[Any], *, full: bool) -> dict[str, Any]:
    shown = items if full else items[:SUMMARY_ITEMS]
    return {"total": len(items), "truncated": len(items) - len(shown), "items": shown}


def real_directory(path: Path) -> bool:
    """Check a structural directory without following its final component."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise ValueError(f"Invalid directory {path}: symlink")
    if not stat.S_ISDIR(mode):
        raise ValueError(f"Invalid directory {path}: expected a real directory")
    return True


def read_optional(path: Path, *, root: Path) -> str | None:
    # Check every checkout-relative parent before opening the leaf. File links
    # remain allowed, but must not turn structural parents into outside reads.
    parent = root
    for component in path.relative_to(root).parts[:-1]:
        parent /= component
        if not real_directory(parent):
            return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"Invalid configuration {path}: expected a regular file")
            with os.fdopen(descriptor, encoding="utf-8", newline="") as stream:
                descriptor = -1  # The stream owns and closes the descriptor now.
                return stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError(f"Invalid configuration {path}: broken symlink") from None
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Invalid configuration {path}: {exc}") from exc


def context_exclusions(path: Path, *, root: Path) -> dict[str, str]:
    """Use maintenance policy rules, but read through the nonblocking file reader."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for atom, reason in pairs:
            if atom in result:
                raise ValueError(f"duplicate atom {atom!r}")
            result[atom] = reason
        return result

    try:
        content = read_optional(path, root=root)
        if content is None:
            return {}
        exclusions = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(exclusions, dict):
            raise UpdatePolicyError(
                f"Invalid update policy {path}: expected an atom-to-reason object"
            )
        validated: dict[str, str] = {}
        for atom, reason in exclusions.items():
            if not EXACT_ATOM_RE.fullmatch(atom) or VERSION_SUFFIX_RE.search(atom.split("/")[-1]):
                raise ValueError(f"{atom!r} must be an exact category/package atom")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"reason for {atom!r} must be a non-empty string")
            validated[atom] = reason
        return validated
    except ValueError as exc:
        raise UpdatePolicyError(f"Invalid update policy {path}: {exc}") from exc


def read_masks(root: Path) -> dict[str, str | None]:
    """Enumerate masks without hiding scan/stat errors or following directory links."""
    if not real_directory(root / "profiles"):
        return {"profiles/package.mask": None}
    mask_path = root / "profiles/package.mask"
    try:
        mode = mask_path.lstat().st_mode
    except FileNotFoundError:
        return {"profiles/package.mask": read_optional(mask_path, root=root)}
    if not stat.S_ISDIR(mode):
        # Preserve file symlink reads. Directory links fail as unreadable files,
        # rather than escaping the mask tree or silently omitting their masks.
        return {"profiles/package.mask": read_optional(mask_path, root=root)}
    pending = [mask_path]
    paths: list[Path] = []
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    pending.append(path)
                else:
                    paths.append(path)
    return {str(path.relative_to(root)): read_optional(path, root=root) for path in sorted(paths)}


def read_layout(path: Path, *, root: Path) -> tuple[dict[str, str], str | None]:
    content = read_optional(path, root=root)
    settings: dict[str, str] = {}
    for number, line in enumerate((content or "").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[a-z][a-z0-9-]*", key) or key in settings:
            raise ValueError(f"Invalid configuration {path}:{number}: invalid or duplicate setting")
        settings[key] = value
    if "thin-manifests" in settings and settings["thin-manifests"] not in {"true", "false"}:
        raise ValueError(f"Invalid configuration {path}: thin-manifests must be true or false")
    if "use-manifests" in settings and settings["use-manifests"] not in {"true", "false", "strict"}:
        raise ValueError(f"Invalid configuration {path}: invalid use-manifests")
    if "sign-manifests" in settings and settings["sign-manifests"] not in {"true", "false"}:
        raise ValueError(f"Invalid configuration {path}: sign-manifests must be true or false")
    for key in (
        "eapis-banned",
        "eapis-deprecated",
        "eapis-testing",
        "profile-eapis-banned",
        "profile-eapis-deprecated",
    ):
        if any(not re.fullmatch(r"[0-9]{1,3}", value) for value in settings.get(key, "").split()):
            raise ValueError(f"Invalid configuration {path}: {key} must contain EAPI numbers")
    for master in settings.get("masters", "").split():
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", master):
            raise ValueError(f"Invalid configuration {path}: invalid master name")
    return settings, content


def checkout_git(root: Path) -> dict[str, Any]:
    """Only inspect this checkout, without index refreshes or fsmonitor hooks."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.hooksPath={os.devnull}",
    ]

    def run(*args: str, directory: Path = root) -> bytes:
        return subprocess.run(
            [*command, "-C", str(directory), *args],
            env=env,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout

    def executable_filters_configured() -> bool:
        # Porcelain status also compares content in populated submodules. Walk
        # their cached gitlinks without refreshing the index or executing shell.
        pending = [root]
        seen: set[Path] = set()
        while pending:
            directory = pending.pop().resolve()
            if directory in seen:
                continue
            seen.add(directory)
            if not directory.is_relative_to(root):
                raise OSError("Submodule worktree escapes checkout")
            git_root = Path(
                os.fsdecode(run("rev-parse", "--show-toplevel", directory=directory).rstrip(b"\n"))
            ).resolve()
            if git_root != directory:
                raise OSError("Submodule Git root does not match worktree")
            # Config reads never run commands. Include local, included and
            # worktree entries, even overridden ones, before comparing content.
            for entry in run("config", "--includes", "--null", "--list", directory=directory).split(
                b"\0"
            ):
                key, separator, value = entry.partition(b"\n")
                if re.fullmatch(rb"filter\..*\.(?:clean|process)", key) and (
                    value or not separator
                ):
                    return True
            for entry in run("ls-files", "--stage", "-z", directory=directory).split(b"\0"):
                metadata, separator, path = entry.partition(b"\t")
                if separator and metadata.startswith(b"160000 "):
                    child = directory / os.fsdecode(path)
                    marker = child / ".git"
                    if marker.exists() or marker.is_symlink():
                        pending.append(child)
        return False

    result: dict[str, Any] = {
        "status": "unchecked",
        "revision": None,
        "dirty": None,
        "reason": None,
    }
    try:
        git_root = Path(os.fsdecode(run("rev-parse", "--show-toplevel").rstrip(b"\n"))).resolve()
        if git_root != root:
            result["reason"] = "Overlay root is not the Git checkout root"
            return result
        if executable_filters_configured():
            result["reason"] = "Git inspection skipped: executable clean/process filter configured"
            return result
        revision = run("rev-parse", "--verify", "HEAD").decode("ascii").strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            result["reason"] = "Git did not return an exact revision"
            return result
        dirty = bool(
            run(
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
                "--ignore-submodules=none",
            )
        )
    except FileNotFoundError:
        result["reason"] = "Git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        result["reason"] = "Git inspection timed out"
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        result["reason"] = "Git inspection failed; checkout or HEAD unavailable"
    else:
        result.update(status="checked", revision=revision, dirty=dirty)
    return result


def inventory_entries(path: Path) -> list[tuple[Path, int]]:
    """Do not suppress inventory scan errors or follow directory/file links."""
    if path.is_symlink():
        raise ValueError(f"Invalid inventory {path}: symlink")
    with os.scandir(path) as entries:
        return [
            (path / entry.name, entry.stat(follow_symlinks=False).st_mode)
            for entry in sorted(entries, key=lambda item: item.name)
        ]


def inventory_packages(root: Path) -> list[tuple[PackageRef, list[Path]]]:
    packages = []
    for category, mode in inventory_entries(root):
        if category.name.startswith(".") or category.name in SKIP_DIRS:
            continue
        if stat.S_ISLNK(mode):
            # Only classify the target. Non-directories cannot be categories;
            # never read their contents or traverse directory-link targets.
            if stat.S_ISDIR(category.stat().st_mode):
                raise ValueError(f"Invalid inventory {category}: directory symlink")
            continue
        if not stat.S_ISDIR(mode):
            continue
        for package, mode in inventory_entries(category):
            if package.name.startswith("."):
                continue
            if stat.S_ISLNK(mode):
                if stat.S_ISDIR(package.stat().st_mode):
                    raise ValueError(f"Invalid inventory {package}: directory symlink")
                continue
            if not stat.S_ISDIR(mode):
                continue
            ebuilds = []
            for path, mode in inventory_entries(package):
                if not path.name.endswith(".ebuild"):
                    continue
                if stat.S_ISLNK(mode):
                    raise ValueError(f"Invalid inventory {path}: symlink")
                try:
                    parse_ebuild_filename(path)
                except EbuildParseError:
                    continue
                ebuilds.append(path)
            if ebuilds:
                packages.append((PackageRef(category.name, package.name, package), ebuilds))
    return packages


def literal_eapi(path: Path, *, root: Path) -> str | None:
    """Read only the first non-comment line, never evaluate shell expressions."""
    content = read_optional(path, root=root)
    if content is None:
        raise ValueError(f"Invalid inventory {path}: ebuild disappeared")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"EAPI=(?:'([0-9]{1,3})'|\"([0-9]{1,3})\"|([0-9]{1,3}))(?:\s+#.*)?", line
        )
        return (
            next((value for value in match.groups() if value is not None), None) if match else None
        )
    return None


def context_root(start: Path) -> Path | None:
    """Do not bypass a nearest overlay marker just because its target is broken."""
    for candidate in (start, *start.parents):
        if not real_directory(candidate / "profiles"):
            continue
        marker = candidate / "profiles/repo_name"
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"Invalid configuration {marker}: {exc}") from exc
        # Validate before returning, so a directory or unreadable symlink fails here.
        if read_optional(marker, root=candidate) is None:
            raise ValueError(f"Invalid configuration {marker}: marker disappeared")
        return candidate
    return None


def generate_context(start: Path, *, full: bool = False) -> dict[str, Any]:
    if not start.is_dir():
        raise ValueError(f"Not a valid Gentoo overlay directory: {start}")
    root = context_root(start)
    if root is None:
        raise ValueError(f"Not a valid Gentoo overlay: {start}; expected profiles/repo_name")
    for directory in ("profiles", "metadata", "eclass"):
        real_directory(root / directory)
    name_path = root / "profiles/repo_name"
    name_content = read_optional(name_path, root=root)
    name = (name_content or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError(f"Invalid configuration {name_path}: expected one repository name")
    layout_path = root / "metadata/layout.conf"
    layout, layout_content = read_layout(layout_path, root=root)
    profile_eapi_path = root / "profiles/eapi"
    profile_content = read_optional(profile_eapi_path, root=root)
    profile_eapi = profile_content.strip() if profile_content is not None else None
    if profile_eapi is not None and not re.fullmatch(r"[0-9]{1,3}", profile_eapi):
        raise ValueError(f"Invalid configuration {profile_eapi_path}: expected an EAPI number")
    packages = sorted(inventory_packages(root), key=lambda item: item[0].atom)
    eapis: Counter[str] = Counter()
    unresolved_eapis = 0
    categories: Counter[str] = Counter()
    category_ebuilds: Counter[str] = Counter()
    policy_path = root / "metadata/update-exclusions.json"
    exclusions = context_exclusions(policy_path, root=root)
    package_items = []
    ebuild_paths: list[str] = []
    for package, ebuilds in packages:
        ebuild_paths.extend(str(path.relative_to(root)) for path in ebuilds)
        package_items.append({"atom": package.atom, "ebuild_count": len(ebuilds)})
        categories[package.category] += 1
        category_ebuilds[package.category] += len(ebuilds)
        for path in ebuilds:
            value = literal_eapi(path, root=root)
            if value is None:
                unresolved_eapis += 1
            else:
                eapis[value] += 1
    atoms = {package.atom for package, _ in packages}
    policy_items = [
        {
            "atom": atom,
            "reason": reason,
            "reason_characters": len(reason),
            "reason_truncated": 0,
            "active": atom in atoms,
        }
        for atom, reason in sorted(exclusions.items())
    ]
    active_items = [item for item in policy_items if item["active"]]
    eclass_dir = root / "eclass"
    eclasses = []
    try:
        eclass_mode = eclass_dir.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISDIR(eclass_mode):
            raise ValueError(f"Invalid inventory {eclass_dir}: expected a real directory")
        for path, mode in inventory_entries(eclass_dir):
            if not path.name.endswith(".eclass"):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f"Invalid inventory {path}: expected a regular non-link file")
            eclasses.append(path.name)
    context = {
        "schema_version": 1,
        "mode": "full" if full else "summary",
        "limits": {
            "items": None if full else SUMMARY_ITEMS,
            "reason_characters": None if full else SUMMARY_REASON_CHARACTERS,
            "string_characters": None if full else SUMMARY_STRING_CHARACTERS,
            "output_bytes": None if full else SUMMARY_OUTPUT_BYTES,
        },
        "checkout": {"root": str(root), "name": name, "git": checkout_git(root)},
        "inventory": {
            "category_count": len(categories),
            "package_count": len(packages),
            "ebuild_count": sum(category_ebuilds.values()),
            "categories": section(
                [
                    {
                        "name": category,
                        "package_count": count,
                        "ebuild_count": category_ebuilds[category],
                    }
                    for category, count in sorted(categories.items())
                ],
                full=full,
            ),
            "packages": section(package_items, full=full),
        },
        "configuration": {
            "layout_path": str(layout_path),
            "layout_present": layout_path.exists(),
            "masters": section(layout.get("masters", "").split(), full=full),
            "manifest": {
                "thin_manifests": layout.get("thin-manifests"),
                "sign_manifests": layout.get("sign-manifests"),
                "use_manifests": layout.get("use-manifests"),
                "hashes": section(layout.get("manifest-hashes", "").split(), full=full),
                "required_hashes": section(
                    layout.get("manifest-required-hashes", "").split(), full=full
                ),
            },
            "eapi": {
                "profile": profile_eapi,
                "unresolved_count": unresolved_eapis,
                "banned": section(layout.get("eapis-banned", "").split(), full=full),
                "deprecated": section(layout.get("eapis-deprecated", "").split(), full=full),
                "testing": section(layout.get("eapis-testing", "").split(), full=full),
                "profile_banned": section(
                    layout.get("profile-eapis-banned", "").split(), full=full
                ),
                "profile_deprecated": section(
                    layout.get("profile-eapis-deprecated", "").split(), full=full
                ),
                "ebuilds": section(
                    [{"value": value, "count": count} for value, count in sorted(eapis.items())],
                    full=full,
                ),
            },
        },
        "local_eclasses": section(eclasses, full=full),
        "update_policy": {
            "path": str(root / "metadata/update-exclusions.json"),
            "present": (root / "metadata/update-exclusions.json").exists(),
            "active_count": len(active_items),
            "exclusions": section(policy_items, full=full),
            "active_exclusions": section(active_items, full=full),
        },
        "verification": [
            {"cwd": str(root), "command": "pkgcheck scan ."},
            {"cwd": str(root), "command": "pkgcheck scan -f latest category/package"},
            {
                "cwd": str(root),
                "command": (
                    f"{TOOLS_PATH}/bin/test-ebuild --overlay-path {shlex.quote(str(root))} "
                    "category/package/package-version.ebuild"
                ),
                "requires_trusted_ebuild": True,
            },
            {"cwd": str(root / TOOLS_PATH), "command": "uv run ruff check ."},
            {"cwd": str(root / TOOLS_PATH), "command": "uv run ruff format --check ."},
            {"cwd": str(root / TOOLS_PATH), "command": "uv run ty check src autopilot"},
            {"cwd": str(root / TOOLS_PATH), "command": "uv run pytest -q"},
        ],
    }
    if full:
        context["inventory"]["ebuilds"] = section(sorted(ebuild_paths), full=True)
        configs: dict[str, str | None] = {
            "profiles/repo_name": name_content,
            "profiles/eapi": profile_content,
            "metadata/layout.conf": layout_content,
        }
        configs.update(read_masks(root))
        context["full_configuration"] = configs
    if not full:
        return bound_strings(context)
    context["string_truncations"] = []
    return context
