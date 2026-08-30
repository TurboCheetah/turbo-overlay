import json

import httpx

from overlay_tools.core.github import GitHubClient, ReleaseInfo


class TestGitHubClient:
    def test_http_client_follows_redirects(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if str(request.url) == "https://api.github.test/start":
                return httpx.Response(302, headers={"Location": "https://api.github.test/final"})
            return httpx.Response(200, json={"ok": True})

        client = GitHubClient()
        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

        response = client.session.get("https://api.github.test/start")

        assert response.json() == {"ok": True}
        assert requests == ["https://api.github.test/start", "https://api.github.test/final"]


class TestGitHubClientChannelLookup:
    def test_nightly_channel_finds_newest_nightly_tag(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "api.github.com" in str(request.url)
            # Older nightly first, with a non-nightly stable tag in between.
            # A "first marker match" implementation would return 20260830;
            # only an explicit newest-selection pass returns 20260831.
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": "v0.0.37-nightly.20260830.1227",
                        "html_url": "https://github.com/x/t3code/releases/tag/v0.0.37-nightly.20260830.1227",
                        "draft": False,
                        "prerelease": True,
                    },
                    {
                        "tag_name": "v0.0.36",
                        "html_url": "https://github.com/x/t3code/releases/tag/v0.0.36",
                        "draft": False,
                        "prerelease": False,
                    },
                    {
                        "tag_name": "v0.0.37-nightly.20260831.0101",
                        "html_url": "https://github.com/x/t3code/releases/tag/v0.0.37-nightly.20260831.0101",
                        "draft": False,
                        "prerelease": True,
                    },
                ],
            )

        client = GitHubClient()
        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        info = client._get_latest_release_for_channel("x/t3code", "nightly")

        assert info is not None
        assert info.tag == "v0.0.37-nightly.20260831.0101"
        assert info.version == "0.0.37-nightly.20260831.0101"

    def test_nightly_channel_sorts_numeric_boundary(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # 0.0.10 is lexically smaller than 0.0.9; newest-selection must
            # compare numerically so the newer 0.0.10 nightly wins.
            return httpx.Response(
                200,
                json=[
                    {"tag_name": "v0.0.9-nightly.20260830.1227", "draft": False},
                    {"tag_name": "v0.0.10-nightly.20260831.0101", "draft": False},
                ],
            )

        client = GitHubClient()
        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        info = client._get_latest_release_for_channel("x/t3code", "nightly")

        assert info is not None
        assert info.tag == "v0.0.10-nightly.20260831.0101"
        assert info.version == "0.0.10-nightly.20260831.0101"

    def test_nightly_channel_reads_channel_cache(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        client = GitHubClient(cache_dir=cache_dir)
        client._write_cache(
            "x/t3code",
            ReleaseInfo(
                tag="v0.0.37-nightly.20260830.1227",
                version="0.0.37-nightly.20260830.1227",
                url="https://github.com/x/t3code/releases/tag/v0.0.37-nightly.20260830.1227",
            ),
            channel="nightly",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(
                "channel lookup must not hit the API when the channel cache is fresh"
            )

        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        info = client.get_latest_release("x/t3code", channel="nightly")

        assert info is not None
        assert info.tag == "v0.0.37-nightly.20260830.1227"
        assert info.version == "0.0.37-nightly.20260830.1227"

    def test_stable_channel_uses_dot_suffix_marker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": "v0.2026.06.03.09.49.stable_00",
                        "html_url": "https://github.com/x/warp/releases/tag/v0.2026.06.03.09.49.stable_00",
                        "draft": False,
                    }
                ],
            )

        client = GitHubClient()
        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        info = client._get_latest_release_for_channel("x/warp", "stable")

        assert info is not None
        assert info.tag == "v0.2026.06.03.09.49.stable_00"
        assert info.version == "0.2026.06.03.09.49.stable_00"

    def test_nightly_channel_skips_drafts(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": "v0.0.37-nightly.20260831.0101",
                        "html_url": "https://github.com/x/t3code/releases/tag/v0.0.37-nightly.20260831.0101",
                        "draft": True,
                    }
                ],
            )

        client = GitHubClient()
        client.session = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        info = client._get_latest_release_for_channel("x/t3code", "nightly")

        assert info is None


class TestGitHubClientCache:
    def test_cache_is_isolated_per_channel(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        client = GitHubClient(cache_dir=cache_dir)

        client._write_cache(
            "pingdotgg/t3code",
            ReleaseInfo(
                tag="v0.0.37-nightly.20260830.1227",
                version="0.0.37-nightly.20260830.1227",
                url="https://github.com/pingdotgg/t3code/releases/tag/v0.0.37-nightly.20260830.1227",
            ),
            channel="nightly",
        )
        client._write_cache(
            "pingdotgg/t3code",
            ReleaseInfo(
                tag="v0.0.36",
                version="0.0.36",
                url="https://github.com/pingdotgg/t3code/releases/tag/v0.0.36",
            ),
        )

        nightly_cached = client._read_cache("pingdotgg/t3code", channel="nightly")
        stable_cached = client._read_cache("pingdotgg/t3code")

        assert nightly_cached is not None
        assert nightly_cached.tag == "v0.0.37-nightly.20260830.1227"
        assert nightly_cached.version == "0.0.37-nightly.20260830.1227"
        assert stable_cached is not None
        assert stable_cached.tag == "v0.0.36"
        assert stable_cached.version == "0.0.36"

    def test_read_cache_re_normalizes_tag(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_path = cache_dir / "example_tool.json"
        cache_path.write_text(
            json.dumps(
                {
                    "tag": "example-tool-1.2.3",
                    "version": "example-tool-1.2.3",
                    "url": "https://github.com/example/tool/releases/tag/example-tool-1.2.3",
                }
            )
        )

        client = GitHubClient(cache_dir=cache_dir)
        cached = client._read_cache("example/tool")

        assert cached is not None
        assert cached.tag == "example-tool-1.2.3"
        assert cached.version == "1.2.3"
