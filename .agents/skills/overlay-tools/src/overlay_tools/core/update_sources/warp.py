from __future__ import annotations

import re

from overlay_tools.core.update_sources.base import (
    PackageSourceContext,
    SourceMatch,
    SourceRelease,
    values_match_host,
)

WARP_CHANGELOG_URL = "https://docs.warp.dev/changelog"


class WarpUpdateSource:
    name = "warp"

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        if values_match_host((context.src_uri, context.homepage), "warp.dev"):
            return SourceMatch(
                source_name=self.name,
                source_url=WARP_CHANGELOG_URL,
                fallback_to_github=True,
            )

        name_parts = [part for part in re.split(r"[^a-z0-9]+", context.name.lower()) if part]
        if "warp" in name_parts:
            return SourceMatch(
                source_name=self.name,
                source_url=WARP_CHANGELOG_URL,
                fallback_to_github=True,
            )

        return None

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        """Warp changelog parsing is not implemented; keep GitHub fallback behavior."""
        return None
