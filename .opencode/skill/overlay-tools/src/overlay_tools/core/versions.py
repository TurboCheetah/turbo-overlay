from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

from packaging import version as pkg_version

from overlay_tools.core.errors import VersionError

SuffixType = Literal["alpha", "beta", "pre", "rc", "p", None]

GENTOO_VERSION_RE = re.compile(
    r"^"
    r"(?P<base>\d+(?:\.\d+)*)"
    r"(?P<letter>[a-z])?"
    r"(?:_(?P<suffix>alpha|beta|pre|rc|p)(?P<suffix_num>\d+)?)?"
    r"(?:-r(?P<revision>\d+))?"
    r"$"
)

STABLE_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.stable_(?P<num>\d+)$")
V_PREFIX_RE = re.compile(r"^[vV]")


@dataclass(frozen=True)
class GentooVersion:
    base: str
    letter: str | None = None
    suffix_type: SuffixType = None
    suffix_num: int | None = None
    revision: int | None = None

    def __str__(self) -> str:
        s = self.base
        if self.letter:
            s += self.letter
        if self.suffix_type:
            s += f"_{self.suffix_type}"
            if self.suffix_num is not None:
                s += str(self.suffix_num)
        if self.revision is not None:
            s += f"-r{self.revision}"
        return s

    def to_pep440(self) -> str:
        s = self.base
        if self.letter:
            s += self.letter
        if self.suffix_type == "alpha":
            s += f"a{self.suffix_num or 0}"
        elif self.suffix_type == "beta":
            s += f"b{self.suffix_num or 0}"
        elif self.suffix_type == "rc":
            s += f"rc{self.suffix_num or 0}"
        elif self.suffix_type == "pre":
            s += f".dev{self.suffix_num or 0}"
        elif self.suffix_type == "p":
            s += f".post{self.suffix_num or 0}"
        return s


def parse_gentoo_version(v: str) -> GentooVersion:
    match = GENTOO_VERSION_RE.match(v)
    if not match:
        raise VersionError(f"Invalid Gentoo version format: {v}")

    suffix = match.group("suffix")
    return GentooVersion(
        base=match.group("base"),
        letter=match.group("letter"),
        suffix_type=cast(SuffixType, suffix) if suffix else None,
        suffix_num=int(match.group("suffix_num")) if match.group("suffix_num") else None,
        revision=int(match.group("revision")) if match.group("revision") else None,
    )


def normalize_gentoo_version(v: str, lenient: bool = False) -> str:
    v = V_PREFIX_RE.sub("", v)

    if lenient:
        stable_match = STABLE_SUFFIX_RE.match(v)
        if stable_match:
            v = f"{stable_match.group('base')}_p{stable_match.group('num')}"

    if GENTOO_VERSION_RE.match(v):
        return v

    if lenient:
        return v

    raise VersionError(f"Invalid Gentoo version format: {v}")


def normalize_upstream_version(tag: str) -> str:
    tag = V_PREFIX_RE.sub("", tag)
    tag = re.sub(r"^release-", "", tag)
    return tag.strip()


def compare_versions(a: str, b: str) -> int:
    try:
        gv_a = parse_gentoo_version(a)
        gv_b = parse_gentoo_version(b)
        pep_a = gv_a.to_pep440()
        pep_b = gv_b.to_pep440()
    except VersionError:
        pep_a = a
        pep_b = b

    try:
        va = pkg_version.parse(pep_a)
        vb = pkg_version.parse(pep_b)
        if va > vb:
            return 1
        elif va < vb:
            return -1
        return 0
    except Exception:
        if a > b:
            return 1
        elif a < b:
            return -1
        return 0


def upstream_to_gentoo(upstream: str, suffix_map: dict[str, str] | None = None) -> str:
    v = normalize_upstream_version(upstream)

    if suffix_map:
        for pattern, replacement in suffix_map.items():
            v = re.sub(pattern, replacement, v)

    stable_match = STABLE_SUFFIX_RE.match(v)
    if stable_match:
        v = f"{stable_match.group('base')}_p{stable_match.group('num')}"

    return v
