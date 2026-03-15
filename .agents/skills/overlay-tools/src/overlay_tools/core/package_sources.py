from __future__ import annotations

import re


VENDOR_URLS: dict[str, str] = {
    "hayase": "https://api.hayase.watch/latest",
    "warp": "https://docs.warp.dev/changelog",
}

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
