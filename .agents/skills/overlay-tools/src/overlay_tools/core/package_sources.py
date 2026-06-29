from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class CustomReleaseInfo:
    version: str
    url: str


VENDOR_URLS: dict[str, str] = {
    "hayase": "https://api.hayase.watch/latest",
    "warp": "https://docs.warp.dev/changelog",
}

HAYASE_LINUX_DEB_RE = re.compile(r"^linux-hayase-(?P<version>[0-9][^-]*)-linux\.deb$")

HOST_URLS: tuple[tuple[str, str], ...] = (
    ("hayase.watch", "https://api.hayase.watch/latest"),
    ("warp.dev", "https://docs.warp.dev/changelog"),
)


def get_custom_url(name: str, src_uri: str | None, homepage: str | None) -> str | None:
    for value in (src_uri, homepage):
        if not value:
            continue

        for host_fragment, url in HOST_URLS:
            if host_fragment in value:
                return url

    name_parts = [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]
    for vendor, url in VENDOR_URLS.items():
        if vendor in name_parts:
            return url

    return None


def parse_hayase_latest(payload: dict[str, Any]) -> CustomReleaseInfo | None:
    """Extract the latest Linux .deb release from Hayase's custom API response."""
    for filename, url in payload.items():
        match = HAYASE_LINUX_DEB_RE.match(filename)
        if not match:
            continue
        return CustomReleaseInfo(version=match.group("version"), url=str(url))
    return None


def get_custom_latest_release(custom_url: str | None) -> CustomReleaseInfo | None:
    if not custom_url or custom_url != VENDOR_URLS["hayase"]:
        return None

    try:
        response = requests.get(custom_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    return parse_hayase_latest(payload)
