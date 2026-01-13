from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from overlay_tools.core.ebuilds import EbuildName, find_ebuilds, select_latest_ebuild


SKIP_DIRS = frozenset({"metadata", "profiles", "licenses", "deprecated", ".git", ".opencode"})


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
