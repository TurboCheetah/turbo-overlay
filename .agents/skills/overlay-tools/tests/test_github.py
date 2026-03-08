import json

from overlay_tools.core.github import GitHubClient


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
