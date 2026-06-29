from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceMatch:
    source_name: str
    source_url: str
    fallback_to_github: bool = False


@dataclass(frozen=True)
class SourceRelease:
    version: str
    url: str


@dataclass(frozen=True)
class PackageSourceContext:
    category: str
    name: str
    src_uri: str | None
    homepage: str | None


class UpdateSource(Protocol):
    name: str

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        """Return source metadata when this plugin applies to a package."""

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        """Return the latest release, or None when lookup fails non-fatally."""


def values_match_host(values: tuple[str | None, ...], hostname: str) -> bool:
    for value in values:
        if not value:
            continue
        for token in value.split():
            host = urlparse(token).hostname or ""
            if host == hostname or host.endswith(f".{hostname}"):
                return True
    return False
