"""Render new binary package starters without fetching or executing anything."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import secrets
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

TEMPLATES = Path(__file__).resolve().parents[3] / "assets" / "templates"
VERSION = r"[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?"
COMPONENT = r"[A-Za-z0-9][A-Za-z0-9+_-]{0,119}"
CATEGORY = r"[A-Za-z0-9][A-Za-z0-9+_.-]{0,119}"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
RESERVED = {"metadata", "profiles", "licenses", "eclass", "distfiles", "packages", "deprecated"}


@dataclass(frozen=True)
class CreatePlan:
    root: Path
    atom: str
    binary: str
    root_identity: tuple[int, int]
    artifacts: dict[str, str]


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def cleanup_close(fd: int, path: Path, errors: list[str]) -> None:
    """Attempt a raw descriptor close once, retaining an exact-path diagnostic."""
    try:
        os.close(fd)
    except OSError as exc:
        # Even EINTR/EIO can follow release and reuse of the descriptor number.
        errors.append(f"{path}: close failed: {exc}; descriptor state uncertain; not retried")


def descriptor_cleanup_error(failure: BaseException | None, errors: list[str]) -> ValueError:
    detail = f"cleanup incomplete, inspect manually: {'; '.join(errors)}"
    return ValueError(f"{failure}; {detail}" if failure is not None else detail)


def open_root(root: Path) -> int:
    """Walk each ancestor without following symlinks, not just the final root."""
    fd = os.open("/", DIRECTORY_FLAGS)
    path = Path("/")
    errors: list[str] = []
    try:
        for part in root.parts[1:]:
            if part == "..":
                raise ValueError("overlay path must not contain '..'")
            next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            # Transfer ownership before attempting the parent close. On failure,
            # only the new child is still ours; the parent must never be retried.
            parent_fd, parent_path = fd, path
            fd, path = next_fd, path / part
            cleanup_close(parent_fd, parent_path, errors)
            if errors:
                raise ValueError("overlay traversal descriptor cleanup failed")
        return fd
    except BaseException as exc:
        cleanup_close(fd, path, errors)
        if errors:
            raise descriptor_cleanup_error(exc, errors) from exc
        raise


def read_marker(root_fd: int, root: Path, directory: str, filename: str) -> str:
    directory_fd = os.open(directory, DIRECTORY_FLAGS, dir_fd=root_fd)
    errors: list[str] = []
    fd: int | None = None
    marker = None
    marker_path = root / directory / filename
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        marker = os.fdopen(fd, "r", encoding="utf-8")
        if not stat.S_ISREG(os.fstat(marker.fileno()).st_mode):
            raise ValueError("overlay markers must be regular files")
        return marker.read(4096)
    finally:
        if marker is None:
            if fd is not None:
                # fdopen did not take ownership. No file object can close it.
                cleanup_close(fd, marker_path, errors)
        else:
            try:
                marker.close()
            except OSError as exc:
                errors.append(
                    f"{marker_path}: close failed: {exc}; descriptor state uncertain; not retried"
                )
        cleanup_close(directory_fd, root / directory, errors)
        if errors:
            raise descriptor_cleanup_error(sys.exception(), errors)


def validate_target(root: Path, category: str, package: str) -> tuple[int, int]:
    fd = open_root(root)
    errors: list[str] = []
    try:
        repo_name = read_marker(fd, root, "profiles", "repo_name").rstrip("\n")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", repo_name):
            raise ValueError("overlay needs a valid profiles/repo_name")
        read_marker(fd, root, "metadata", "layout.conf")
        try:
            category_fd = os.open(category, DIRECTORY_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            return identity(os.fstat(fd))
        try:
            try:
                os.stat(package, dir_fd=category_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("package target already exists; refusing any overwrite")
        finally:
            cleanup_close(category_fd, root / category, errors)
            if errors:
                raise descriptor_cleanup_error(sys.exception(), errors)
        return identity(os.fstat(fd))
    finally:
        # Category errors have already been attached to the active exception.
        root_errors: list[str] = []
        cleanup_close(fd, root, root_errors)
        if root_errors:
            raise descriptor_cleanup_error(sys.exception(), root_errors)


def validate_text(label: str, value: str, maximum: int) -> None:
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
        or any(char in value for char in "$`\\;|")
        or "@@" in value
    ):
        raise ValueError(f"{label} must be nonempty, single-line literal text, max {maximum} chars")


def validate_url(value: str) -> None:
    allowed = r"[A-Za-z0-9._~:/?&=+,%@-]+"
    parts = urlsplit(value)
    if (
        len(value) > 2048
        or not re.fullmatch(allowed, value)
        or parts.scheme != "https"
        or not parts.hostname
        or parts.netloc != parts.hostname
        or parts.fragment
        or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", parts.hostname)
        or urlunsplit(parts) != value
        or re.search(r"%(?![A-Fa-f0-9]{2})", value)
        or not re.fullmatch(allowed, unquote(value))
        or any(segment in {".", ".."} for segment in unquote(parts.path).split("/"))
    ):
        raise ValueError(
            "URLs must be literal canonical HTTPS URLs without credentials or fragments"
        )


def quote(value: str) -> str:
    """Always single-quote ebuild data, including apostrophes."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_template(name: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    placeholder = re.compile(r"@@([A-Z][A-Z0-9_]*)@@")
    # Validate only the original template. Inserted URL data may contain @@.
    if "@@" in placeholder.sub("", text) or any(
        match[1] not in values for match in placeholder.finditer(text)
    ):
        raise ValueError(f"unresolved template placeholder in {name}")
    return placeholder.sub(lambda match: values[match[1]], text)


def direct_suffix(url: str) -> str:
    """Infer an unpacking hint, not content type, without changing the URL."""
    endings = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip")
    parts = urlsplit(url)
    path_suffix = next((ending for ending in endings if parts.path.endswith(ending)), None)
    if path_suffix is not None:
        return path_suffix
    hints = {
        next((ending for ending in endings if value.endswith(ending)), ".bin")
        for key, value in parse_qsl(parts.query)
        if key in {"file", "filename"}
    }
    if len(hints) > 1:
        raise ValueError("conflicting archive filename hints; review the upstream URL manually")
    return next(iter(hints), ".bin")


def build_plan(
    *,
    root: Path,
    atom: str,
    version: str,
    template: str,
    upstream_url: str,
    license_name: str,
    description: str,
    homepage: str,
    maintainer_email: str,
    maintainer_name: str | None,
    binary_name: str | None,
    keywords: str,
) -> CreatePlan:
    if not re.fullmatch(f"{CATEGORY}/{COMPONENT}", atom):
        raise ValueError("atom must be an exact unversioned category/package")
    category, package = atom.split("/")
    if category in RESERVED or re.search(f"-({VERSION})$", package):
        raise ValueError("reserved category or versioned package atom")
    if len(version) > 100 or not re.fullmatch(VERSION, version):
        raise ValueError("version must use Gentoo version syntax, without normalization")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9+_.-]*(?: [A-Za-z0-9][A-Za-z0-9+_.-]*)*", license_name
    ):
        raise ValueError("license must be one or more space-separated license identifiers")
    validate_text("description", description, 80)
    validate_url(upstream_url)
    validate_url(homepage)
    if not re.fullmatch(
        r"[A-Za-z0-9_+'-]+(?:\.[A-Za-z0-9_+'-]+)*@[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}",
        maintainer_email,
    ):
        raise ValueError("maintainer-email must be an explicit plain email address")
    if maintainer_name is not None:
        validate_text("maintainer-name", maintainer_name, 120)
    binary = binary_name if binary_name is not None else package.removesuffix("-bin")
    if not re.fullmatch(COMPONENT, binary):
        raise ValueError("binary-name must be a single safe filename, not a path or option")
    if keywords and not re.fullmatch(r"~?[a-z][a-z0-9_-]*(?: ~?[a-z][a-z0-9_-]*)*", keywords):
        raise ValueError("keywords must be explicit space-separated architecture tokens")
    root = root.absolute()
    root_identity = validate_target(root, category, package)
    suffix = direct_suffix(upstream_url) if template == "binary-direct" else ".bin"
    archive = suffix != ".bin"
    values = {
        "DESCRIPTION": quote(description),
        "HOMEPAGE": quote(homepage),
        "UPSTREAM_URL": upstream_url,
        "LICENSE": quote(license_name),
        "KEYWORDS": quote(keywords),
        "SUFFIX": suffix,
        "UNPACK": "" if archive else "src_unpack() { :; }\n",
        "LAYOUT_NOTE": "# TODO: adjust S and the binary path for the actual archive layout."
        if archive
        else "# Install the downloaded single binary without unpacking it.",
        "BINARY_SOURCE": f"${{S}}/{binary}" if archive else "${DISTDIR}/${P}.bin",
        "BINARY_NAME": binary,
        "BINARY_QUOTED": quote(binary),
        "QA_PREBUILT": quote(f"usr/bin/{binary}"),
    }
    metadata = render_template(
        "metadata.xml.in",
        {
            "EMAIL": escape(maintainer_email),
            "NAME": f"\t\t<name>{escape(maintainer_name)}</name>\n" if maintainer_name else "",
        },
    )
    return CreatePlan(
        root,
        atom,
        binary,
        root_identity,
        {
            f"{package}-{version}.ebuild": render_template(f"{template}.ebuild.in", values),
            "metadata.xml": metadata,
        },
    )


def rename_exclusive(directory_fd: int, source: str, target: str) -> None:
    """Publish the whole package atomically on Linux, never replace even an empty dir."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "safe writes need Linux renameat2 support") from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory_fd, os.fsencode(source), directory_fd, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target)


def same_entry(parent_fd: int, name: str, expected: tuple[int, int]) -> bool:
    try:
        return identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == expected
    except FileNotFoundError:
        return False


def check_attached(plan: CreatePlan, category_fd: int, category: str) -> None:
    root_fd = open_root(plan.root)
    errors: list[str] = []
    try:
        if identity(os.fstat(root_fd)) != plan.root_identity or not same_entry(
            root_fd, category, identity(os.fstat(category_fd))
        ):
            raise ValueError("overlay/category changed during creation; refusing publication")
    finally:
        cleanup_close(root_fd, plan.root, errors)
        if errors:
            raise descriptor_cleanup_error(sys.exception(), errors)


def write_plan(plan: CreatePlan) -> None:
    """Prepare in a private directory, then atomically publish with NOREPLACE.

    Directory-relative operations never traverse a swapped category/package link.
    Cleanup is nonrecursive and removes only recorded inodes. Unknown entries
    are preserved and reported rather than deleting another writer's work.
    """
    category, package = plan.atom.split("/")
    stage = f".create-ebuild-{secrets.token_hex(16)}"
    root_fd = open_root(plan.root)
    category_fd: int | None = None
    stage_fd: int | None = None
    category_owned = False
    category_identity: tuple[int, int] | None = None
    published = False
    stage_identity: tuple[int, int] | None = None
    stage_owned = False
    created_files: dict[str, tuple[int, int]] = {}
    cleanup_errors: list[str] = []
    failure: Exception | None = None

    try:
        if identity(os.fstat(root_fd)) != plan.root_identity:
            raise ValueError("overlay root changed during creation")
        try:
            os.mkdir(category, 0o755, dir_fd=root_fd)
            category_owned = True
            category_identity = identity(os.stat(category, dir_fd=root_fd, follow_symlinks=False))
        except FileExistsError:
            pass
        category_fd = os.open(category, DIRECTORY_FLAGS, dir_fd=root_fd)
        opened_category_identity = identity(os.fstat(category_fd))
        if category_owned and opened_category_identity != category_identity:
            raise ValueError("new category changed during creation")
        if not category_owned:
            category_identity = opened_category_identity
        check_attached(plan, category_fd, category)
        try:
            os.stat(package, dir_fd=category_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("package target already exists; refusing any overwrite")
        os.mkdir(stage, 0o700, dir_fd=category_fd)
        stage_owned = True
        stage_identity = identity(os.stat(stage, dir_fd=category_fd, follow_symlinks=False))
        stage_fd = os.open(stage, DIRECTORY_FLAGS, dir_fd=category_fd)
        if identity(os.fstat(stage_fd)) != stage_identity:
            raise ValueError("private staging directory changed during creation")
        for filename, content in plan.artifacts.items():
            fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
                dir_fd=stage_fd,
            )
            output = None
            artifact_path = plan.root / category / stage / filename
            try:
                created_files[filename] = identity(os.fstat(fd))
                output = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            finally:
                if output is None:
                    # fstat/fdopen failed before the file object took ownership.
                    cleanup_close(fd, artifact_path, cleanup_errors)
                else:
                    try:
                        output.close()
                    except OSError as exc:
                        cleanup_errors.append(
                            f"{artifact_path}: close failed: {exc}; "
                            "descriptor state uncertain; not retried"
                        )
            if cleanup_errors:
                raise ValueError("artifact descriptor cleanup failed before publication")
        # Make the staged directory entry durable before publication, matching
        # the per-file fsync above.
        os.fsync(category_fd)
        os.fchmod(stage_fd, 0o755)
        check_attached(plan, category_fd, category)
        if not same_entry(category_fd, stage, stage_identity):
            raise ValueError("private staging directory changed; refusing publication")
        # Reopen the anchored directory to avoid a stale pre-write directory cursor.
        scan_fd = os.open(".", DIRECTORY_FLAGS, dir_fd=stage_fd)
        try:
            if set(os.listdir(scan_fd)) != set(plan.artifacts):
                raise ValueError("unexpected staging artifacts; refusing publication")
        finally:
            cleanup_close(scan_fd, plan.root / category / stage, cleanup_errors)
        if cleanup_errors:
            raise ValueError("staging descriptor cleanup failed before publication")
        for filename, expected in created_files.items():
            if not same_entry(stage_fd, filename, expected):
                raise ValueError("staging file changed; refusing publication")
        rename_exclusive(category_fd, stage, package)
        # The publish only survives power loss if the containing directory
        # entry is synced, matching the per-file fsync above.
        os.fsync(category_fd)
        published = True
        check_attached(plan, category_fd, category)
        if not same_entry(category_fd, package, stage_identity):
            raise ValueError("published package changed; inspect the target manually")
    except (OSError, ValueError) as exc:
        failure = exc
    finally:
        directory = package if published else stage
        directory_path = plan.root / category / directory
        if failure is not None:
            if stage_owned and stage_identity is None:
                cleanup_errors.append(f"{directory_path} identity unknown; left untouched")
            if category_owned and category_identity is None:
                cleanup_errors.append(f"{plan.root / category} identity unknown; left untouched")
        if failure is not None and stage_fd is not None:
            for filename, expected in created_files.items():
                try:
                    if same_entry(stage_fd, filename, expected):
                        os.unlink(filename, dir_fd=stage_fd)
                except OSError as exc:
                    cleanup_errors.append(f"{directory_path / filename}: {exc}")
            try:
                scan_fd = os.open(".", DIRECTORY_FLAGS, dir_fd=stage_fd)
                try:
                    cleanup_errors.extend(
                        f"{directory_path / name} left untouched"
                        for name in sorted(os.listdir(scan_fd))
                    )
                finally:
                    cleanup_close(scan_fd, directory_path, cleanup_errors)
            except OSError as exc:
                cleanup_errors.append(f"{directory_path}: cannot inspect residual entries: {exc}")
        if stage_fd is not None:
            cleanup_close(stage_fd, directory_path, cleanup_errors)
        if category_fd is not None:
            if failure is not None and stage_identity is not None:
                try:
                    if same_entry(category_fd, directory, stage_identity):
                        os.rmdir(directory, dir_fd=category_fd)
                        # Match the per-file durability intent for rollback.
                        os.fsync(category_fd)
                    else:
                        cleanup_errors.append(f"{directory_path} moved or replaced; left untouched")
                except OSError as exc:
                    cleanup_errors.append(f"{directory_path}: {exc}")
            cleanup_close(category_fd, plan.root / category, cleanup_errors)
        if failure is not None and category_identity is not None:
            try:
                if same_entry(root_fd, category, category_identity):
                    if category_owned:
                        os.rmdir(category, dir_fd=root_fd)
                else:
                    cleanup_errors.append(
                        f"{plan.root / category} moved or replaced; left untouched"
                    )
            except OSError as exc:
                if exc.errno == errno.ENOTEMPTY:
                    # Preserve the other writer's entry, keep the diagnostic.
                    cleanup_errors.append(
                        f"{plan.root / category} not empty; residual entries left untouched"
                    )
                else:
                    cleanup_errors.append(f"{plan.root / category}: {exc}")
        cleanup_close(root_fd, plan.root, cleanup_errors)
    detail = f"; cleanup incomplete, inspect manually: {'; '.join(cleanup_errors)}"
    if failure is not None:
        raise ValueError(f"creation failed: {failure}" + (detail if cleanup_errors else ""))
    if cleanup_errors:
        raise ValueError(f"published starter retained at {directory_path}" + detail)


def report(plan: CreatePlan, *, written: bool = False) -> str:
    lines = ["Written starter only." if written else "Preview only. Use --write to create files."]
    for filename, content in plan.artifacts.items():
        lines.extend([f"--- {plan.atom}/{filename} ---", content.rstrip()])
    ebuild = next(name for name in plan.artifacts if name.endswith(".ebuild"))
    lines.extend(
        [
            "Not an installable package yet. Review trusted upstream layout and dependencies.",
            "Review an immutable release-specific upstream URL; syntax cannot prove immutability.",
            "URL suffixes are unpacking hints only; inspect artifact type and set S/binary path.",
            "Missing artifacts: Manifest, any required desktop/icon assets; cache not generated.",
            "Review license availability and redistribution rights; add dependencies and assets.",
            "Only after review, run these yourself from the overlay root:",
            f"  ebuild {plan.atom}/{ebuild} manifest",
            f"  pkgcheck scan -f latest {plan.atom}",
            (
                "  .agents/skills/overlay-tools/bin/test-ebuild --overlay-path "
                f"{shlex.quote(str(plan.root))} --expect usr/bin/{plan.binary} {plan.atom}/{ebuild}"
            ),
            "No downloads, Manifest/cache writes, ebuild execution or host installation performed.",
        ]
    )
    return "\n".join(lines)
