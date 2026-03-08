from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from overlay_tools.core.versions import normalize_upstream_version


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
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "overlay-tools/0.1"
        if token:
            self.session.headers["Authorization"] = f"token {token}"

        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, repo: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{repo.replace('/', '_')}.json"

    def _read_cache(self, repo: str) -> ReleaseInfo | None:
        cache_path = self._get_cache_path(repo)
        if not cache_path or not cache_path.exists():
            return None

        cache_age = time.time() - cache_path.stat().st_mtime
        if cache_age >= CACHE_TTL_SECONDS:
            return None

        try:
            data = json.loads(cache_path.read_text())
            return ReleaseInfo(
                tag=data["tag"],
                version=data["version"],
                url=data.get("url", ""),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _write_cache(self, repo: str, info: ReleaseInfo) -> None:
        cache_path = self._get_cache_path(repo)
        if not cache_path:
            return
        try:
            cache_path.write_text(
                json.dumps({"tag": info.tag, "version": info.version, "url": info.url})
            )
        except OSError:
            pass

    def get_latest_release(self, repo: str) -> ReleaseInfo | None:
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

        except requests.RequestException as e:
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
        except (requests.RequestException, ValueError):
            return None

    def get_rate_limit(self) -> dict:
        try:
            response = self.session.get(f"{self.API_BASE}/rate_limit", timeout=5)
            response.raise_for_status()
            return response.json().get("rate", {})
        except (requests.RequestException, ValueError):
            return {}


class GitHubAPIError(Exception):
    pass


class GitHubRateLimitError(GitHubAPIError):
    pass
