from pathlib import Path

from overlay_tools.cli import check_updates
from overlay_tools.core.ebuilds import EbuildName
from overlay_tools.core.github import GitHubClient
from overlay_tools.core.package_sources import CustomReleaseInfo


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


class TestCheckChannelEbuildCustomRelease:
    def test_returns_update_available_for_custom_release(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)

        monkeypatch.setattr(
            check_updates,
            "get_custom_latest_release",
            lambda custom_url: CustomReleaseInfo(
                version="6.4.79",
                url="https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb",
            ),
        )

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, GitHubClient()
        )

        assert status.status == "update-available"
        assert status.current_version == "6.4.60"
        assert status.latest_version == "6.4.79"
        assert status.gentoo_version == "6.4.79"
        assert status.latest_url == "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb"
        assert status.custom_url == "https://api.hayase.watch/latest"

    def test_custom_release_none_falls_back_to_manual_check(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "hayase-bin-6.4.60.ebuild"
        write_ebuild(ebuild_path)
        ebuild = EbuildName("hayase-bin", "6.4.60", ebuild_path)

        monkeypatch.setattr(check_updates, "get_custom_latest_release", lambda custom_url: None)

        status = check_updates.check_channel_ebuild(
            "media-video", "hayase-bin", ebuild, pkg_path, GitHubClient()
        )

        assert status.status == "manual-check"
        assert status.current_version == "6.4.60"
        assert status.latest_version is None
        assert status.custom_url == "https://api.hayase.watch/latest"
