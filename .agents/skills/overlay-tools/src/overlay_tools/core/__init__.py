"""Core modules for overlay-tools."""

from overlay_tools.core.ebuilds import (
    parse_ebuild_filename,
    read_ebuild_vars,
    update_ebuild_var,
)
from overlay_tools.core.errors import (
    EbuildParseError,
    ExternalToolMissingError,
    OverlayToolsError,
    VersionError,
)
from overlay_tools.core.versions import (
    GentooVersion,
    compare_versions,
    normalize_gentoo_version,
    parse_gentoo_version,
)

__all__ = [
    "EbuildParseError",
    "ExternalToolMissingError",
    "GentooVersion",
    "OverlayToolsError",
    "VersionError",
    "compare_versions",
    "normalize_gentoo_version",
    "parse_ebuild_filename",
    "parse_gentoo_version",
    "read_ebuild_vars",
    "update_ebuild_var",
]
