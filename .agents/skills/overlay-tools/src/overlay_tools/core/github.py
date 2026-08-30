from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from overlay_tools.core.versions import (
    compare_versions,
    normalize_upstream_version,
    upstream_to_gentoo,
)

GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+/[^/]+)")
CACHE_TTL_SECONDS = 1800


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    url: str


def extract_github_repo(src_uri: str | None = None, metadata_xml: str | None = None) -> str | None:
    if src_uri:
        match = GITHUB_REPO_RE.search(src_uri)
        if match:
            repo = match.group(1)
            repo = re.sub(r"\.git$", "", repo)
            repo = re.sub(r"/releases.*", "", repo)
            repo = re.sub(r"/archive.*", "", repo)
            repo = re.sub(r"/raw.*", "", repo)
            return repo

    if metadata_xml:
        try:
            root = ET.fromstring(metadata_xml)
            for remote_id in root.findall('.//remote-id[@type="github"]'):
                if remote_id.text:
                    return remote_id.text.strip()
        except ET.ParseError:
            pass

    return None


def extract_github_repo_from_path(metadata_path: Path) -> str | None:
    if not metadata_path.exists():
        return None
    try:
        return extract_github_repo(metadata_xml=metadata_path.read_text())
    except OSError:
        return None


class GitHubClient:
    API_BASE = "https://api.github.com"

    def __init__(self, token: str | None = None, cache_dir: Path | None = None):
        self.token = token
        self.cache_dir = cache_dir
        self.session = httpx.Client(follow_redirects=True)
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "overlay-tools/0.1"
        if token:
            self.session.headers["Authorization"] = f"token {token}"

        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, repo: str, channel: str | None = None) -> Path | None:
        if not self.cache_dir:
            return None
        key = repo.replace("/", "_")
        if channel:
            key = f"{key}.{channel}"
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, repo: str, channel: str | None = None) -> ReleaseInfo | None:
        cache_path = self._get_cache_path(repo, channel)
        if not cache_path or not cache_path.exists():
            return None

        cache_age = time.time() - cache_path.stat().st_mtime
        if cache_age >= CACHE_TTL_SECONDS:
            return None

        try:
            data = json.loads(cache_path.read_text())
            tag = data["tag"]
            return ReleaseInfo(
                tag=tag,
                # Re-normalize from the tag so stale cache entries remain valid
                # after normalization logic changes.
                version=normalize_upstream_version(tag),
                url=data.get("url", ""),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _write_cache(self, repo: str, info: ReleaseInfo, channel: str | None = None) -> None:
        cache_path = self._get_cache_path(repo, channel)
        if not cache_path:
            return
        with contextlib.suppress(OSError):
            cache_path.write_text(
                json.dumps({"tag": info.tag, "version": info.version, "url": info.url})
            )

    def get_latest_release(self, repo: str, channel: str | None = None) -> ReleaseInfo | None:
        if channel is not None:
            # Channel-aware: fetch recent releases and pick the latest matching
            # this channel. Cache is keyed by channel so a nightly lookup never
            # poisons the stable-channel result (or vice versa).
            return self._get_latest_release_for_channel(repo, channel)

        cached = self._read_cache(repo)
        if cached:
            return cached

        url = f"{self.API_BASE}/repos/{repo}/releases/latest"

        try:
            response = self.session.get(url, timeout=10)

            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining", "0")
                if remaining == "0":
                    reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                    wait_minutes = max(0, (reset_time - time.time()) / 60)
                    raise GitHubRateLimitError(
                        f"Rate limit exceeded. Resets in {wait_minutes:.0f} minutes."
                    )

            if response.status_code == 404:
                return self.get_latest_tag(repo)

            response.raise_for_status()
            data = response.json()

            tag = data.get("tag_name", "")
            info = ReleaseInfo(
                tag=tag,
                version=normalize_upstream_version(tag),
                url=data.get("html_url", ""),
            )

            self._write_cache(repo, info)
            return info

        except httpx.HTTPError as e:
            raise GitHubAPIError(f"API error for {repo}: {e}") from e
        except ValueError as e:
            raise GitHubAPIError(f"Invalid JSON response for {repo}: {e}") from e

    def _get_latest_release_for_channel(self, repo: str, channel: str) -> ReleaseInfo | None:
        cached = self._read_cache(repo, channel=channel)
        if cached:
            return cached

        # Nightly tags use a hyphen marker (e.g. 0.0.37-nightly.20260830.1227);
        # other channels use a dot-suffix marker like .stable_ or .preview_.
        tag_marker = "-nightly." if channel == "nightly" else f".{channel}_"
        url = f"{self.API_BASE}/repos/{repo}/releases?per_page=30"
        try:
            response = self.session.get(url, timeout=10)

            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining", "0")
                if remaining == "0":
                    reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                    wait_minutes = max(0, (reset_time - time.time()) / 60)
                    raise GitHubRateLimitError(
                        f"Rate limit exceeded. Resets in {wait_minutes:.0f} minutes."
                    )

            response.raise_for_status()
            releases = response.json()

            best: ReleaseInfo | None = None
            for release in releases:
                if release.get("draft"):
                    continue
                tag = release.get("tag_name", "")
                if tag_marker not in tag:
                    continue
                info = ReleaseInfo(
                    tag=tag,
                    version=normalize_upstream_version(tag),
                    url=release.get("html_url", ""),
                )
                # GitHub returns releases newest-first, but select the newest
                # explicitly rather than trusting list order. Compare in
                # Gentoo space so numeric boundaries (0.0.9 -> 0.0.10) sort
                # correctly instead of raw lexical order.
                if (
                    best is None
                    or compare_versions(
                        upstream_to_gentoo(best.version),
                        upstream_to_gentoo(info.version),
                    )
                    < 0
                ):
                    best = info

            if best is not None:
                self._write_cache(repo, best, channel=channel)
            return best

        except httpx.HTTPError as e:
            raise GitHubAPIError(f"API error for {repo}: {e}") from e
        except ValueError as e:
            raise GitHubAPIError(f"Invalid JSON response for {repo}: {e}") from e

    def get_latest_tag(self, repo: str) -> ReleaseInfo | None:
        url = f"{self.API_BASE}/repos/{repo}/tags"
        try:
            response = self.session.get(url, timeout=10, params={"per_page": 1})
            response.raise_for_status()
            tags = response.json()
            if not tags:
                return None

            tag = tags[0].get("name", "")
            return ReleaseInfo(
                tag=tag,
                version=normalize_upstream_version(tag),
                url=f"https://github.com/{repo}/releases/tag/{tag}",
            )
        except (httpx.HTTPError, ValueError):
            return None

    def get_rate_limit(self) -> dict:
        try:
            response = self.session.get(f"{self.API_BASE}/rate_limit", timeout=5)
            response.raise_for_status()
            return response.json().get("rate", {})
        except (httpx.HTTPError, ValueError):
            return {}


class GitHubAPIError(Exception):
    pass


class GitHubRateLimitError(GitHubAPIError):
    pass
