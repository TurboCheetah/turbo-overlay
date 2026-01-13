"""Core modules for overlay-tools."""

from overlay_tools.core.errors import (
    OverlayToolsError,
    VersionError,
    EbuildParseError,
    ExternalToolMissingError,
)
from overlay_tools.core.versions import (
    normalize_gentoo_version,
    compare_versions,
    parse_gentoo_version,
    GentooVersion,
)
from overlay_tools.core.ebuilds import (
    parse_ebuild_filename,
    read_ebuild_vars,
    update_ebuild_var,
)

__all__ = [
    # errors
    "OverlayToolsError",
    "VersionError",
    "EbuildParseError",
    "ExternalToolMissingError",
    # versions
    "normalize_gentoo_version",
    "compare_versions",
    "parse_gentoo_version",
    "GentooVersion",
    # ebuilds
    "parse_ebuild_filename",
    "read_ebuild_vars",
    "update_ebuild_var",
]
