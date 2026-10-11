"""Local, read-only package QA. Never interpret ebuild metadata as Bash values."""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

ManifestData = tuple[list[tuple[str, Path, str, list[str]]], set[tuple[str, str]]]
LOCAL_CHECKS = ("metadata_xml", "manifest_local", "cache", "ebuild_advisory", "bash_syntax")


class QAInputError(ValueError):
    """Invalid input or an unsafe filesystem boundary."""

    def __init__(self, message: str, path: Path | str = "") -> None:
        super().__init__(message)
        self.path = str(path)


class QAReadError(QAInputError):
    """An input read failed at a known path and check."""

    def __init__(self, path: Path, check: str, exc: Exception) -> None:
        super().__init__(f"Cannot read {path}: {exc}", path)
        self.check = check


def read_bytes(path: Path, check: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise QAReadError(path, check, exc) from exc


def read_text(path: Path, check: str) -> str:
    content = read_bytes(path, check)
    try:
        return content.decode("utf-8")
    except UnicodeError as exc:
        raise QAReadError(path, check, exc) from exc


@dataclass
class Finding:
    check: str
    path: str
    message: str
    severity: str = "error"


@dataclass
class Report:
    packages: list[str] = field(default_factory=list)
    selected_packages: list[str] = field(default_factory=list)
    package_coverage: dict[str, dict[str, str]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"check": "remote_dist", "reason": "DIST bytes are not fetched or verified"},
            {"check": "bash_metadata", "reason": "No sourcing or expansion of SRC_URI or eclasses"},
            {"check": "pkgcheck", "reason": "Not requested; use --pkgcheck explicitly"},
        ]
    )
    coverage: dict[str, str] = field(
        default_factory=lambda: {
            "manifest_local": "not-run",
            "metadata_xml": "not-run",
            "cache": "not-run",
            "ebuild_advisory": "not-run",
            "bash_syntax": "not-run",
            "pkgcheck": "not-run",
            "remote_dist": "unchecked",
            "bash_metadata": "unchecked",
        }
    )
    inconclusive: bool = False

    @property
    def exit_code(self) -> int:
        if self.inconclusive or not self.packages:
            return 2
        return int(any(f.severity == "error" for f in self.findings))

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "examined_count": len(self.packages),
            "selected_count": len(self.selected_packages),
            "status": {0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE"}[self.exit_code],
        }

    def add(self, check: str, path: Path | str, message: str, severity: str = "error") -> None:
        self.findings.append(Finding(check, str(path), message, severity))


def resolve_path(path: Path) -> Path:
    """Reject loops on Python 3.11 and 3.14, but allow absent QA input paths."""
    try:
        try:
            return path.resolve(strict=True)
        except FileNotFoundError:
            return path.resolve()
    except RuntimeError as exc:
        # Python 3.11's pathlib raises RuntimeError for loops, not OSError.
        # Keep this conversion at the resolve call; do not mask programming bugs.
        if not str(exc).startswith("Symlink loop from "):
            raise
        raise QAInputError(f"Cannot resolve path: {path}: {exc}") from exc


def safe_path(root: Path, path: Path) -> Path:
    """Validate before opening a candidate, including its existing parents."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise QAInputError(f"Outside overlay: {path}") from exc
    if any(part in {"..", "."} for part in relative.parts):
        raise QAInputError(f"Traversal is not allowed: {path}")
    if not resolve_path(path).is_relative_to(root):
        raise QAInputError(f"Symlink escapes overlay: {path}")
    if path.exists():
        mode = path.stat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise QAInputError(f"Special files are not allowed: {path}")
    return path


def validate_root(raw: str) -> Path:
    root = resolve_path(Path(raw).absolute())
    if not root.is_dir():
        raise QAInputError(f"Overlay is not a directory: {root}")
    for name in ["profiles/repo_name", "metadata/layout.conf"]:
        path = safe_path(root, root / name)
        if not path.is_file():
            raise QAInputError(f"Missing overlay marker: {name}")
    return root


def select_explicit(root: Path, targets: list[str]) -> list[str]:
    selected = set()
    for target in targets:
        path = safe_path(root, Path(target) if Path(target).is_absolute() else root / target)
        if not path.exists():
            raise QAInputError(f"Target does not exist: {target}")
        parts = path.relative_to(root).parts
        if len(parts) == 4 and parts[:2] == ("metadata", "md5-cache"):
            matches = [
                atom
                for atom in select_all(root)
                if atom.split("/", 1)[0] == parts[2]
                and re.fullmatch(re.escape(atom.split("/", 1)[1]) + r"-[0-9].*", parts[3])
            ]
            if len(matches) != 1:
                raise QAInputError(f"Cannot map cache target to one package: {target}")
            selected.add(matches[0])
            continue
        if len(parts) < 2 or not all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", p) for p in parts[:2]
        ):
            raise QAInputError(f"Not a package target: {target}")
        package = root.joinpath(*parts[:2])
        if not package.is_dir() or not list(package.glob("*.ebuild")):
            raise QAInputError(f"No ebuilds in package: {target}")
        if len(parts) > 2 and not (
            parts[2] in {"Manifest", "metadata.xml", "files"}
            or len(parts) == 3
            and parts[2].endswith(".ebuild")
        ):
            raise QAInputError(f"Unsupported package path: {target}")
        selected.add("/".join(parts[:2]))
    return sorted(selected)


def select_all(root: Path) -> list[str]:
    selected = []
    reserved = {"metadata", "profiles", "eclass", "licenses", "distfiles", "packages"}
    for category in sorted(root.iterdir()):
        if category.name.startswith(".") or category.name in reserved or not category.is_dir():
            continue
        safe_path(root, category)
        for package in sorted(category.iterdir()):
            safe_path(root, package)
            if package.is_dir() and list(package.glob("*.ebuild")):
                selected.extend(select_explicit(root, [str(package)]))
    return sorted(set(selected))


def git_output(root: Path, arguments: list[str]) -> bytes:
    # Bind selection to this checkout, not an inherited GIT_DIR/index/object store.
    # Missing promisor objects must fail locally rather than launch a lazy fetch.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                # Diff's automatic refresh can write even with GIT_OPTIONAL_LOCKS=0.
                "diff.autoRefreshIndex=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                f"core.excludesFile={os.devnull}",
                "-c",
                f"core.attributesFile={os.devnull}",
                "-c",
                "protocol.allow=never",
                "-c",
                "submodule.recurse=false",
                "-C",
                str(root),
                *arguments,
            ],
            capture_output=True,
            timeout=30,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QAInputError(f"Git is unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise QAInputError(result.stderr.decode(errors="replace").strip() or "Git selection failed")
    return result.stdout


def preflight_git_worktrees(root: Path, report: Report) -> None:
    """Inspect cached gitlinks and effective config before any worktree comparison.

    submodule.recurse=false does not stop diff from inspecting dirty children.
    ls-files --stage reads the index without refreshing or converting files.
    """
    pending = [root]
    visited = set()
    while pending:
        worktree = pending.pop()
        if worktree in visited:
            raise QAInputError(f"Repeated submodule worktree: {worktree}", worktree)
        visited.add(worktree)
        actual = resolve_path(
            Path(os.fsdecode(git_output(worktree, ["rev-parse", "--show-toplevel"])).strip())
        )
        if actual != worktree:
            raise QAInputError(f"Invalid populated submodule worktree: {worktree}", worktree)
        keys = git_output(worktree, ["config", "--includes", "--null", "--name-only", "--list"])
        if any(
            key.lower().startswith(b"filter.") and key.lower().endswith((b".clean", b".process"))
            for key in keys.split(b"\x00")
        ):
            report.skipped.append(
                {
                    "check": "selection",
                    "path": str(worktree),
                    "reason": (
                        "Git clean/process filters configured; select explicit targets or --all"
                    ),
                }
            )
            raise QAInputError(
                "Unsafe Git change selection: clean/process filter configuration found in "
                f"{worktree}; no diff ran. Use explicit targets or --all for local QA.",
                worktree,
            )
        entries = git_output(worktree, ["ls-files", "--stage", "-z"])
        for entry in entries.split(b"\x00"):
            if not entry:
                continue
            metadata, separator, raw_path = entry.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                raise QAInputError(f"Malformed Git index entry in {worktree}", worktree)
            if fields[0] != b"160000":
                continue
            name = os.fsdecode(raw_path)
            if (
                Path(name).is_absolute()
                or not name
                or any(part in {"..", ".", ""} for part in name.split("/"))
                or fields[2] != b"0"
            ):
                raise QAInputError(f"Unsafe or unmerged submodule path: {name}", worktree)
            child = safe_path(root, worktree / name)
            resolved = resolve_path(child)
            if resolved == worktree or not resolved.is_relative_to(worktree):
                raise QAInputError(f"Submodule escapes its parent worktree: {child}", child)
            marker = safe_path(root, child / ".git")
            if marker.exists():
                if not child.is_dir():
                    raise QAInputError(f"Invalid populated submodule: {child}", child)
                pending.append(resolved)


def select_changed(root: Path, ref: str, report: Report) -> list[str]:
    if not ref.strip() or ref.startswith("-") or any(c in ref for c in "\x00\n\r"):
        raise QAInputError("Invalid Git base reference")
    git_root = resolve_path(
        Path(os.fsdecode(git_output(root, ["rev-parse", "--show-toplevel"])).strip())
    )
    if git_root != root:
        raise QAInputError("--changed-since requires the overlay to be the Git worktree root")
    # Mark preflight/ref/diff failures unchecked. Only the completed union below
    # can claim authoritative selection. Every child uses git_output isolation.
    report.coverage["selection"] = "unchecked"
    preflight_git_worktrees(root, report)
    base = (
        git_output(root, ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"])
        .decode()
        .strip()
    )
    paths = set()
    for revisions in [[base], ["--cached", base], []]:
        output = git_output(
            root,
            [
                "diff",
                # Name-only/raw output trusts stale stat entries with auto-refresh
                # disabled. Numstat verifies Git-normalized content without writing
                # the index, and still reports binary, mode and type changes.
                "--numstat",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                *revisions,
                "--",
                ".",
            ],
        )
        for record in output.split(b"\x00"):
            if not record:
                continue
            # --no-renames gives one path per record. Split only the two stat
            # separators so literal tabs/newlines and non-UTF-8 paths survive.
            fields = record.split(b"\t", 2)
            if len(fields) != 3 or not fields[2]:
                raise QAInputError("Malformed Git numstat selection output")
            paths.add(os.fsdecode(fields[2]))
    untracked = git_output(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    for name in untracked.split(b"\x00"):
        if name:
            report.skipped.append(
                {
                    "check": "untracked",
                    "path": os.fsdecode(name),
                    "reason": "Not in Git diff; select explicitly or use --all",
                }
            )
    packages = select_paths(root, sorted(paths), report)
    report.coverage["selection"] = "git-base-plus-index-and-worktree"
    return packages


def select_paths(root: Path, paths: list[str], report: Report) -> list[str]:
    """Map exact QA input paths, including deleted files, to surviving packages."""
    available = select_all(root)
    selected = set()
    for name in paths:
        if not name or Path(name).is_absolute() or any(p in {"..", "."} for p in name.split("/")):
            raise QAInputError(f"Invalid repository-relative changed path: {name}")
        safe_path(root, root / name)
        parts = Path(name).parts
        matches = set()
        if name == "metadata/layout.conf" or parts[0] in {"eclass", "profiles"}:
            matches.update(available)
        elif len(parts) == 4 and parts[:2] == ("metadata", "md5-cache"):
            matches.update(
                atom
                for atom in available
                if atom.split("/")[0] == parts[2]
                and re.fullmatch(re.escape(atom.split("/")[1]) + r"-[0-9].*", parts[3])
            )
        elif (
            len(parts) >= 3
            and "/".join(parts[:2]) in available
            and (
                (
                    len(parts) == 3
                    and (parts[2].endswith(".ebuild") or parts[2] in {"Manifest", "metadata.xml"})
                )
                or parts[2] == "files"
            )
        ):
            matches.add("/".join(parts[:2]))
        selected.update(matches)
        if not matches:
            report.skipped.append(
                {
                    "check": "changed_path",
                    "path": name,
                    "reason": "No current package maps to this QA input path",
                }
            )
    return sorted(selected)


def preflight(root: Path, packages: list[str]) -> None:
    """Validate every selected tree before any package content is read."""
    safe_path(root, root / "metadata/md5-cache")
    for atom in packages:
        package = safe_path(root, root / atom)
        for path in package.rglob("*"):
            safe_path(root, path)
        cache_dir = safe_path(root, root / "metadata/md5-cache" / package.parent.name)
        if cache_dir.exists():
            for path in cache_dir.iterdir():
                if path.name.startswith(package.name + "-"):
                    safe_path(root, path)


def check_package(
    root: Path, atom: str, report: Report, thin: bool, manifest_data: ManifestData | None
) -> None:
    package = root / atom
    xml = package / "metadata.xml"
    if not xml.is_file():
        report.add("metadata_xml", xml, "Missing metadata.xml")
    else:
        content = read_bytes(xml, "metadata_xml")
        try:
            document = ET.fromstring(content)
        except (ET.ParseError, LookupError, ValueError) as exc:
            # Expat rejects unknown or unsupported declared encodings separately.
            report.add("metadata_xml", xml, f"Invalid XML: {exc}")
        else:
            if document.tag != "pkgmetadata":
                report.add("metadata_xml", xml, "Expected pkgmetadata root element")
    report.coverage["metadata_xml"] = "checked"
    check_manifest(package, report, thin, manifest_data)
    ebuilds = sorted(package.glob("*.ebuild"))
    cache_dir = root / "metadata/md5-cache" / package.parent.name
    for ebuild in ebuilds:
        cache = cache_dir / ebuild.stem
        if not cache.is_file():
            report.add("cache", cache, "Missing metadata cache")
            report.coverage["cache"] = "partial"
            continue
        values = {}
        for line in read_text(cache, "cache").splitlines():
            key, sep, value = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in values:
                report.add("cache", cache, "Malformed or duplicate cache field")
                continue
            values[key] = value
        digest = values.get("_md5_", "")
        if not re.fullmatch(r"[0-9a-f]{32}", digest):
            report.add("cache", cache, "Missing or malformed _md5_ cache fingerprint")
        elif digest != hashlib.md5(read_bytes(ebuild, "cache")).hexdigest():
            report.add("cache", cache, "Stale ebuild _md5_ cache fingerprint")
        report.coverage["cache"] = "partial"
    if cache_dir.is_dir():
        expected = {ebuild.stem for ebuild in ebuilds}
        for cache in sorted(cache_dir.iterdir()):
            # Match Gentoo version starts, not another package with the same prefix.
            if (
                re.fullmatch(re.escape(package.name) + r"-[0-9].*", cache.name)
                and cache.name not in expected
            ):
                report.add("cache", cache, "Orphan metadata cache without an ebuild")
    report.coverage["cache"] = "checked-local-ebuild-fingerprints"
    for ebuild in ebuilds:
        check_ebuild(root, ebuild, report)


def check_ebuild(root: Path, ebuild: Path, report: Report) -> None:
    text = read_text(ebuild, "ebuild_advisory")
    if (
        not text.startswith("# Copyright")
        or "GNU General Public License v2" not in text.split("EAPI", 1)[0]
    ):
        report.add(
            "ebuild_advisory", ebuild, "Missing conventional Gentoo license header", "advisory"
        )
    if not re.search(r"^EAPI=['\"]?[0-9]+['\"]?\s*$", text, re.MULTILINE):
        report.add(
            "ebuild_advisory", ebuild, "No simple literal EAPI assignment; not expanded", "advisory"
        )
    report.coverage["ebuild_advisory"] = "literal-header-and-eapi-only"
    bash = shutil.which("bash")
    if not bash:
        report.inconclusive = True
        report.coverage["bash_syntax"] = (
            "partial" if report.coverage["bash_syntax"] in {"checked", "partial"} else "unchecked"
        )
        report.add("bash_syntax", ebuild, "bash is unavailable; syntax unchecked")
        return
    # -n reads stdin but never executes it. Strip startup hooks from the environment.
    environment = {
        k: v for k, v in os.environ.items() if k not in {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"}
    }
    try:
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-n"],
            input=text,
            text=True,
            capture_output=True,
            cwd=root,
            env=environment,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.inconclusive = True
        report.coverage["bash_syntax"] = (
            "partial" if report.coverage["bash_syntax"] in {"checked", "partial"} else "unchecked"
        )
        report.add("bash_syntax", ebuild, f"Bash syntax unchecked: {exc}")
        return
    report.coverage["bash_syntax"] = (
        "checked" if report.coverage["bash_syntax"] in {"not-run", "checked"} else "partial"
    )
    if result.returncode:
        report.add("bash_syntax", ebuild, result.stderr.strip() or "Bash syntax check failed")


def read_manifest(root: Path, package: Path, report: Report, thin: bool) -> ManifestData | None:
    manifest = package / "Manifest"
    if not manifest.is_file():
        if not thin:
            report.add("manifest_local", manifest, "Missing Manifest")
        return None
    entries = []
    seen = set()
    for number, line in enumerate(read_text(manifest, "manifest_local").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if (
            len(fields) < 5
            or len(fields) % 2 != 1
            or fields[0] not in {"EBUILD", "AUX", "MISC", "DIST"}
        ):
            report.add("manifest_local", manifest, f"Malformed record on line {number}")
            continue
        kind, name, size = fields[:3]
        # Validate names even for DIST. Never open a Manifest-provided absolute/traversal path.
        if Path(name).is_absolute() or any(p in {"..", "."} for p in name.split("/")):
            raise QAInputError(f"Unsafe Manifest name on line {number}: {name}")
        base = package / "files" if kind == "AUX" else package
        path = safe_path(root, base / name)
        if kind != "AUX" and "/" in name:
            raise QAInputError(f"Unexpected Manifest path on line {number}: {name}")
        if (kind, name) in seen:
            report.add("manifest_local", manifest, f"Duplicate {kind} record: {name}")
        seen.add((kind, name))
        entries.append((kind, path, size, fields[3:]))
    return entries, seen


def check_manifest(
    package: Path, report: Report, thin: bool, manifest_data: ManifestData | None
) -> None:
    if manifest_data is None:
        report.coverage["manifest_local"] = "unchecked" if thin else "missing-manifest"
        if thin:
            report.skipped.append(
                {
                    "check": "manifest_local_coverage",
                    "path": str(package / "Manifest"),
                    "reason": "Absent thin Manifest; local hashes are unchecked, DIST not inferred",
                }
            )
        return
    entries, seen = manifest_data
    manifest = package / "Manifest"
    # All referenced paths have been validated before reading any of them.
    for kind, path, size, hashes in entries:
        try:
            expected_size = int(size)
            if expected_size < 0:
                raise ValueError
        except ValueError:
            report.add("manifest_local", manifest, f"Invalid size for {path.name}")
            continue
        if kind == "DIST":
            continue
        if not path.is_file():
            report.add("manifest_local", path, "Manifest entry has no local file")
            continue
        content = read_bytes(path, "manifest_local")
        if len(content) != expected_size:
            report.add("manifest_local", path, "Manifest size mismatch")
        for algorithm, digest in zip(hashes[::2], hashes[1::2], strict=True):
            try:
                actual = hashlib.new(algorithm.lower(), content).hexdigest()
            except (ValueError, TypeError):
                report.add("manifest_local", path, f"Unsupported hash: {algorithm}")
                continue
            if actual != digest.lower():
                report.add("manifest_local", path, f"Manifest {algorithm} mismatch")
    if not thin:
        local = [("EBUILD", p.name) for p in package.glob("*.ebuild")]
        local.extend(
            ("MISC", p.name)
            for p in package.iterdir()
            if p.is_file() and p.name != "Manifest" and not p.name.endswith(".ebuild")
        )
        local.extend(
            ("AUX", str(p.relative_to(package / "files")))
            for p in (package / "files").rglob("*")
            if p.is_file()
        )
        for kind, name in local:
            if (kind, name) not in seen:
                report.add("manifest_local", package / name, f"Missing {kind} Manifest record")
    if report.coverage["manifest_local"] not in {"unchecked", "missing-manifest"}:
        report.coverage["manifest_local"] = "thin-records-only" if thin else "full-local"


def run_pkgcheck(root: Path, packages: list[str], report: Report) -> None:
    report.skipped = [item for item in report.skipped if item["check"] != "pkgcheck"]
    executable = shutil.which("pkgcheck")
    if not executable:
        report.coverage["pkgcheck"] = "unavailable"
        report.inconclusive = True
        report.add("pkgcheck", "", "pkgcheck is unavailable; nothing was installed")
        return
    arguments = [
        executable,
        "scan",
        "--config",
        "no",
        "--repo",
        str(root),
        "--cache",
        "no",
        "--sandbox",
        "yes",
        "--exit",
        "error",
        "-f",
        "latest",
        *packages,
    ]
    try:
        result = subprocess.run(
            arguments, cwd=root, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.coverage["pkgcheck"] = "unavailable"
        report.inconclusive = True
        report.add("pkgcheck", "", str(exc))
        return
    report.coverage["pkgcheck"] = "external-latest-scan"
    if result.stdout.strip():
        report.add(
            "pkgcheck", "", result.stdout.strip(), "error" if result.returncode == 1 else "advisory"
        )
    if result.returncode:
        if result.returncode != 1:
            report.inconclusive = True
            report.coverage["pkgcheck"] = "failed"
        report.add("pkgcheck", "", result.stderr.strip() or f"pkgcheck exited {result.returncode}")
    elif result.stderr.strip():
        report.add("pkgcheck", "", result.stderr.strip(), "advisory")


def run_local(root: Path, packages: list[str], report: Report) -> None:
    report.selected_packages = list(packages)
    report.package_coverage = {atom: dict.fromkeys(LOCAL_CHECKS, "not-run") for atom in packages}
    preflight(root, packages)
    thin = False
    for line in read_text(root / "metadata/layout.conf", "selection").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "thin-manifests":
            value = value.split("#", 1)[0].strip()
            if value not in {"true", "false"}:
                raise QAInputError("thin-manifests must be true or false")
            thin = value == "true"
    if thin:
        report.skipped.append(
            {
                "check": "manifest_local_coverage",
                "reason": "thin-manifests = true; absent local records are not required",
            }
        )
    manifests = {atom: read_manifest(root, root / atom, report, thin) for atom in packages}
    for atom in packages:
        local = Report(skipped=[])
        try:
            check_package(root, atom, local, thin, manifests[atom])
        except (QAReadError, OSError) as exc:
            path = exc.path if isinstance(exc, QAReadError) else exc.filename or str(root / atom)
            if isinstance(exc, QAReadError):
                local.coverage[exc.check] = (
                    "unchecked" if local.coverage[exc.check] == "not-run" else "partial"
                )
                if exc.check == "ebuild_advisory" and local.coverage["bash_syntax"] == "checked":
                    local.coverage["bash_syntax"] = "partial"
            report.skipped.append(
                {
                    "check": "package_scan",
                    "path": str(root / atom),
                    "reason": f"Package scan incomplete; interrupted at {path}",
                }
            )
            raise QAInputError(f"Package {atom} scan incomplete: {exc}", path) from exc
        finally:
            report.findings.extend(local.findings)
            report.skipped.extend(local.skipped)
            report.inconclusive |= local.inconclusive
            report.package_coverage[atom] = {check: local.coverage[check] for check in LOCAL_CHECKS}
            for check in LOCAL_CHECKS:
                statuses = {coverage[check] for coverage in report.package_coverage.values()}
                if len(statuses) == 1:
                    report.coverage[check] = statuses.pop()
                elif (
                    check == "bash_syntax"
                    and statuses <= {"unchecked", "not-run"}
                    or check == "manifest_local"
                    and "unchecked" in statuses
                ):
                    report.coverage[check] = "unchecked"
                elif check == "manifest_local" and "missing-manifest" in statuses:
                    report.coverage[check] = "missing-manifest"
                else:
                    report.coverage[check] = "partial"
        report.packages.append(atom)
