from pathlib import Path

from overlay_tools.cli import check_updates
from overlay_tools.core.ebuilds import EbuildName
from overlay_tools.core.github import GitHubClient, ReleaseInfo
from overlay_tools.core.update_sources.base import PackageSourceContext, SourceMatch, SourceRelease


class FakeSource:
    name = "fake"

    def __init__(self, release: SourceRelease | None, *, fallback_to_github: bool = False):
        self.release = release
        self.fallback_to_github = fallback_to_github
        self.latest_release_calls: list[SourceMatch] = []

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        if context.name != "hayase-bin":
            return None
        return SourceMatch(
            source_name=self.name,
            source_url="https://example.invalid/latest",
            fallback_to_github=self.fallback_to_github,
        )

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        self.latest_release_calls.append(match)
        return self.release


def write_ebuild(path: Path, *, homepage: str = "https://hayase.watch/") -> None:
    path.write_text(
        "\n".join(
            [
                "EAPI=8",
                f'HOMEPAGE="{homepage}"',
                'SRC_URI="https://api.hayase.watch/files/linux-hayase-${PV}-linux.deb"',
            ]
        )
    )


class TestCheckChannelEbuildUpdateSource:
    def test_returns_update_available_for_plugin_release(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)
        source = FakeSource(
            SourceRelease(
                version="6.4.79",
                url="https://example.invalid/hayase.deb",
            )
        )
        monkeypatch.setattr(check_updates, "DEFAULT_UPDATE_SOURCES", (source,))

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, GitHubClient()
        )

        assert status.status == "update-available"
        assert status.current_version == "6.4.60"
        assert status.latest_version == "6.4.79"
        assert status.gentoo_version == "6.4.79"
        assert status.latest_url == "https://example.invalid/hayase.deb"
        assert status.custom_url == "https://example.invalid/latest"
        assert source.latest_release_calls == [
            SourceMatch(source_name="fake", source_url="https://example.invalid/latest")
        ]

    def test_plugin_release_none_falls_back_to_manual_check(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)
        source = FakeSource(None)
        monkeypatch.setattr(check_updates, "DEFAULT_UPDATE_SOURCES", (source,))

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, GitHubClient()
        )

        assert status.status == "manual-check"
        assert status.current_version == "6.4.60"
        assert status.latest_version is None
        assert status.custom_url == "https://example.invalid/latest"
        assert source.latest_release_calls == [
            SourceMatch(source_name="fake", source_url="https://example.invalid/latest")
        ]

    def test_plugin_release_none_does_not_fall_back_to_github(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        (pkg_path / "metadata.xml").write_text(
            "<pkgmetadata><upstream>"
            '<remote-id type="github">example/repo</remote-id>'
            "</upstream></pkgmetadata>"
        )
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)
        source = FakeSource(None)
        monkeypatch.setattr(check_updates, "DEFAULT_UPDATE_SOURCES", (source,))

        class FailIfCalledGitHubClient:
            def get_latest_release(self, repo: str, channel: str | None = None):
                raise AssertionError("GitHub fallback should not run for failed custom source")

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, FailIfCalledGitHubClient()
        )

        assert status.status == "manual-check"
        assert status.github_repo == "example/repo"
        assert status.custom_url == "https://example.invalid/latest"

    def test_plugin_release_none_can_fall_back_to_github_when_allowed(
        self, monkeypatch, tmp_path: Path
    ):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        (pkg_path / "metadata.xml").write_text(
            "<pkgmetadata><upstream>"
            '<remote-id type="github">example/repo</remote-id>'
            "</upstream></pkgmetadata>"
        )
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)
        source = FakeSource(None, fallback_to_github=True)
        monkeypatch.setattr(check_updates, "DEFAULT_UPDATE_SOURCES", (source,))

        class FakeGitHubClient:
            def get_latest_release(self, repo: str, channel: str | None = None):
                assert repo == "example/repo"
                return ReleaseInfo(
                    tag="v6.4.61",
                    version="6.4.61",
                    url="https://github.com/example/repo/releases/tag/v6.4.61",
                )

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, FakeGitHubClient()
        )

        assert status.status == "update-available"
        assert status.latest_version == "6.4.61"
        assert status.custom_url == "https://example.invalid/latest"
