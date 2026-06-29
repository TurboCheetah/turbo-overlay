from __future__ import annotations

from overlay_tools.core.update_sources.base import PackageSourceContext, SourceMatch, UpdateSource
from overlay_tools.core.update_sources.hayase import HayaseUpdateSource
from overlay_tools.core.update_sources.warp import WarpUpdateSource

DEFAULT_UPDATE_SOURCES: tuple[UpdateSource, ...] = (
    HayaseUpdateSource(),
    WarpUpdateSource(),
)


def find_source_match(
    context: PackageSourceContext,
    sources: tuple[UpdateSource, ...] | None = None,
) -> tuple[UpdateSource, SourceMatch] | None:
    if sources is None:
        sources = DEFAULT_UPDATE_SOURCES

    for source in sources:
        match = source.match(context)
        if match is not None:
            return source, match
    return None
