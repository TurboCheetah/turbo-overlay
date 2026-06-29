from pathlib import Path

from overlay_tools.cli import check_updates
from overlay_tools.core.ebuilds import EbuildName
from overlay_tools.core.github import GitHubClient
from overlay_tools.core.update_sources.base import PackageSourceContext, SourceMatch, SourceRelease


class FakeSource:
    name = "fake"

    def __init__(self, release: SourceRelease | None):
        self.release = release
        self.latest_release_calls: list[SourceMatch] = []

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        if context.name != "hayase-bin":
            return None
        return SourceMatch(source_name=self.name, source_url="https://example.invalid/latest")

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
