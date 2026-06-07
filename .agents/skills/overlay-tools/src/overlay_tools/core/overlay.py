from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cmp_to_key
import os
from pathlib import Path

from overlay_tools.core.ebuilds import EbuildName, find_ebuilds, select_latest_ebuild
from overlay_tools.core.versions import compare_versions


KEEP_VERSIONS_ENV_VAR = "OVERLAY_TOOLS_KEEP_VERSIONS"
DEFAULT_KEEP_VERSIONS = 3


SKIP_DIRS = frozenset(
    {"metadata", "profiles", "licenses", "deprecated", ".git", ".opencode", ".agents"}
)


@dataclass
class PackageRef:
    category: str
    name: str
    path: Path

    @property
    def atom(self) -> str:
        return f"{self.category}/{self.name}"


def find_overlay_root(start: Path) -> Path | None:
    current = start.resolve()
    while current != current.parent:
        if (current / "profiles" / "repo_name").exists():
            return current
        current = current.parent
    return None


def find_packages(overlay_root: Path) -> list[PackageRef]:
    packages: list[PackageRef] = []

    for category_dir in overlay_root.iterdir():
        if not category_dir.is_dir():
            continue
        if category_dir.name.startswith(".") or category_dir.name in SKIP_DIRS:
            continue

        for pkg_dir in category_dir.iterdir():
            if not pkg_dir.is_dir():
                continue
            if pkg_dir.name.startswith("."):
                continue

            ebuilds = find_ebuilds(pkg_dir)
            if not ebuilds:
                continue

            packages.append(
                PackageRef(
                    category=category_dir.name,
                    name=pkg_dir.name,
                    path=pkg_dir,
                )
            )

    return packages


def get_package_latest_ebuild(pkg_path: Path, exclude_live: bool = True) -> EbuildName | None:
    ebuilds = find_ebuilds(pkg_path)
    return select_latest_ebuild(ebuilds, exclude_live=exclude_live)


def read_repo_name(repo_root: Path) -> str | None:
    repo_name_path = repo_root / "profiles" / "repo_name"
    if not repo_name_path.is_file():
        return None

    repo_name = repo_name_path.read_text(encoding="utf-8").strip()
    return repo_name or None


def metadata_cache_path(repo_root: Path, category: str, name: str, version: str) -> Path:
    return repo_root / "metadata" / "md5-cache" / category / f"{name}-{version}"


def retention_count_from_env(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw_value = env.get(KEEP_VERSIONS_ENV_VAR)
    if raw_value is None or raw_value == "":
        return DEFAULT_KEEP_VERSIONS

    try:
        keep_versions = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{KEEP_VERSIONS_ENV_VAR} must be a positive integer") from exc

    if keep_versions < 1:
        raise ValueError(f"{KEEP_VERSIONS_ENV_VAR} must be a positive integer")

    return keep_versions


def select_ebuilds_to_drop(
    ebuilds: list[EbuildName], *, keep_versions: int | None = None, versions_to_add: int = 1
) -> tuple[EbuildName, ...]:
    keep_versions = retention_count_from_env() if keep_versions is None else keep_versions
    if keep_versions < 1:
        raise ValueError("keep_versions must be a positive integer")
    if versions_to_add < 0:
        raise ValueError("versions_to_add must be zero or greater")

    non_live_ebuilds = [ebuild for ebuild in ebuilds if "9999" not in ebuild.pv]
    drop_count = len(non_live_ebuilds) + versions_to_add - keep_versions
    if drop_count <= 0:
        return ()

    sorted_ebuilds = sorted(
        non_live_ebuilds, key=cmp_to_key(lambda a, b: compare_versions(a.pv, b.pv))
    )
    return tuple(sorted_ebuilds[:drop_count])


def select_ebuild_to_drop(
    ebuilds: list[EbuildName], *, keep_versions: int | None = None
) -> EbuildName | None:
    drop_ebuilds = select_ebuilds_to_drop(ebuilds, keep_versions=keep_versions)
    return drop_ebuilds[0] if drop_ebuilds else None
