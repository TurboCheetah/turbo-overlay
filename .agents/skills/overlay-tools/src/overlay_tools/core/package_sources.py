from __future__ import annotations


VENDOR_URLS: dict[str, str] = {
    "hayase": "https://hayase.watch/",
    "warp": "https://www.warp.dev/changelog",
}

HOST_URLS: tuple[tuple[str, str], ...] = (
    ("hayase.watch", "https://hayase.watch/"),
    ("warp.dev", "https://www.warp.dev/changelog"),
)


def get_custom_url(name: str, src_uri: str | None, homepage: str | None) -> str | None:
    for value in (src_uri, homepage):
        if not value:
            continue

        for host_fragment, url in HOST_URLS:
            if host_fragment in value:
                return url

    name_lower = name.lower()
    for vendor, url in VENDOR_URLS.items():
        if vendor in name_lower:
            return url

    return None
