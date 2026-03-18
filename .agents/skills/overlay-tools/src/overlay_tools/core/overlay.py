from __future__ import annotations

from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path

from overlay_tools.core.ebuilds import EbuildName, find_ebuilds, select_latest_ebuild
from overlay_tools.core.versions import compare_versions


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


def select_ebuild_to_drop(ebuilds: list[EbuildName], *, min_to_keep: int = 4) -> EbuildName | None:
    non_live_ebuilds = [ebuild for ebuild in ebuilds if "9999" not in ebuild.pv]
    if len(non_live_ebuilds) < min_to_keep:
        return None

    return min(non_live_ebuilds, key=cmp_to_key(lambda a, b: compare_versions(a.pv, b.pv)))
