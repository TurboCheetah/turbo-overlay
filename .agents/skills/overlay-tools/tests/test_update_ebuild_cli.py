from pathlib import Path

from overlay_tools.cli.update_ebuild import metadata_cache_path, read_repo_name


class TestReadRepoName:
    def test_reads_profiles_repo_name(self, tmp_path: Path):
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "repo_name").write_text("turbo-overlay\n", encoding="utf-8")

        assert read_repo_name(tmp_path) == "turbo-overlay"

    def test_returns_none_when_repo_name_missing(self, tmp_path: Path):
        assert read_repo_name(tmp_path) is None


class TestMetadataCachePath:
    def test_builds_expected_path(self, tmp_path: Path):
        path = metadata_cache_path(tmp_path, "media-video", "hayase-bin", "6.4.56")

        assert path == tmp_path / "metadata" / "md5-cache" / "media-video" / "hayase-bin-6.4.56"
