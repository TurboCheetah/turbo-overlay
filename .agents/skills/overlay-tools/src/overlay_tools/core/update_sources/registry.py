from __future__ import annotations

from overlay_tools.core.update_sources.base import PackageSourceContext, SourceMatch, UpdateSource
from overlay_tools.core.update_sources.hayase import HayaseUpdateSource

DEFAULT_UPDATE_SOURCES: tuple[UpdateSource, ...] = (HayaseUpdateSource(),)


def find_source_match(
    context: PackageSourceContext,
    sources: tuple[UpdateSource, ...] = DEFAULT_UPDATE_SOURCES,
) -> tuple[UpdateSource, SourceMatch] | None:
    for source in sources:
        match = source.match(context)
        if match is not None:
            return source, match
    return None
