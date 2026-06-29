from __future__ import annotations

import re

from overlay_tools.core.update_sources.base import PackageSourceContext, SourceMatch, SourceRelease

WARP_CHANGELOG_URL = "https://docs.warp.dev/changelog"


class WarpUpdateSource:
    name = "warp"

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        values = (context.src_uri, context.homepage)
        if any(value and "warp.dev" in value for value in values):
            return SourceMatch(source_name=self.name, source_url=WARP_CHANGELOG_URL)

        name_parts = [part for part in re.split(r"[^a-z0-9]+", context.name.lower()) if part]
        if "warp" in name_parts:
            return SourceMatch(source_name=self.name, source_url=WARP_CHANGELOG_URL)

        return None

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        """Warp changelog parsing is not implemented; keep manual-check fallback behavior."""
        return None
