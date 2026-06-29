from __future__ import annotations

import re
from typing import Any

import httpx

from overlay_tools.core.update_sources.base import (
    PackageSourceContext,
    SourceMatch,
    SourceRelease,
    values_match_host,
)
from overlay_tools.core.versions import compare_versions

HAYASE_API_URL = "https://api.hayase.watch/latest"
HAYASE_LINUX_DEB_RE = re.compile(r"^linux-hayase-(?P<version>[0-9][^-]*)-linux\.deb$")


class HayaseUpdateSource:
    name = "hayase"

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        if values_match_host((context.src_uri, context.homepage), "hayase.watch"):
            return SourceMatch(source_name=self.name, source_url=HAYASE_API_URL)

        name_parts = [part for part in re.split(r"[^a-z0-9]+", context.name.lower()) if part]
        if "hayase" in name_parts:
            return SourceMatch(source_name=self.name, source_url=HAYASE_API_URL)

        return None

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        try:
            response = httpx.get(match.source_url, timeout=10, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        if not isinstance(payload, dict):
            return None
        return parse_latest(payload)


def parse_latest(payload: dict[str, Any]) -> SourceRelease | None:
    """Extract the latest Linux .deb release from Hayase's custom API response."""
    best: SourceRelease | None = None
    for filename, url in payload.items():
        match = HAYASE_LINUX_DEB_RE.match(filename)
        if not match:
            continue
        if not isinstance(url, str) or not url:
            continue
        candidate = SourceRelease(version=match.group("version"), url=url)
        if best is None or compare_versions(best.version, candidate.version) < 0:
            best = candidate
    return best
