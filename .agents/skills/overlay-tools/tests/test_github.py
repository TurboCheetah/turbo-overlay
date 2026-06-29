import json

import httpx

from overlay_tools.core.github import GitHubClient


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


class TestGitHubClientCache:
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
