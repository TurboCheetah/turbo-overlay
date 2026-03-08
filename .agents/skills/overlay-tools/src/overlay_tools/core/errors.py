from __future__ import annotations


class OverlayToolsError(Exception):
    pass


class VersionError(OverlayToolsError):
    pass


class EbuildParseError(OverlayToolsError):
    pass


class ExternalToolMissingError(OverlayToolsError):
    def __init__(self, tool: str, install_hint: str | None = None):
        self.tool = tool
        self.install_hint = install_hint
        msg = f"Required tool not found: {tool}"
        if install_hint:
            msg += f" (install: {install_hint})"
        super().__init__(msg)
