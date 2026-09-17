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


def write_nightly_ebuild(path: Path, *, version: str, my_pv: str) -> None:
    path.write_text(
        "\n".join(
            [
                "EAPI=8",
                f'MY_PV="{my_pv}"',
                'HOMEPAGE="https://t3.codes/"',
                'SRC_URI="https://github.com/pingdotgg/t3code/releases/download/v${MY_PV}/T3-Code-${MY_PV}-x86_64.AppImage"',
            ]
        )
    )


def write_pkg_ebuild(path: Path, *, my_pv: str | None = None) -> None:
    lines = ["EAPI=8"]
    if my_pv is not None:
        lines.append(f'MY_PV="{my_pv}"')
    lines.append(
        'SRC_URI="https://github.com/pingdotgg/t3code/releases/download/v${MY_PV}/x.AppImage"'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


class TestSelectChannel:
    def test_prefers_channel_with_highest_version(self, tmp_path: Path):
        pkg_path = tmp_path / "x11-terms" / "warp"
        write_pkg_ebuild(
            pkg_path / "warp-0.2026.05.20.09.21_pre00.ebuild",
            my_pv="0.2026.05.20.09.21.preview_00",
        )
        write_pkg_ebuild(
            pkg_path / "warp-0.2026.06.03.09.49_p00.ebuild",
            my_pv="0.2026.06.03.09.49.stable_00",
        )

        selected = check_updates.select_channel(check_updates.find_ebuilds(pkg_path))

        assert selected is not None
        channel, ebuild = selected
        assert channel == "stable"
        assert ebuild.pv == "0.2026.06.03.09.49_p00"

    def test_returns_none_for_empty_ebuild_list(self):
        assert check_updates.select_channel([]) is None


def make_overlay(tmp_path: Path) -> Path:
    root = tmp_path / "overlay"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "repo_name").write_text("turbo-overlay\n")
    return root


class TestFilterPackagesByChannel:
    def test_include_keeps_only_matching_channel(self, tmp_path: Path):
        root = make_overlay(tmp_path)
        write_pkg_ebuild(root / "dev-util/t3code-bin/t3code-bin-0.0.40.ebuild")
        write_pkg_ebuild(
            root / "dev-util/t3code-nightly-bin/t3code-nightly-bin-0.0.37_pre202608301227.ebuild",
            my_pv="0.0.37-nightly.20260830.1227",
        )
        packages = check_updates.find_packages(root)

        kept = check_updates.filter_packages_by_channel(packages, include=["nightly"])

        assert [p.atom for p in kept] == ["dev-util/t3code-nightly-bin"]

    def test_exclude_drops_matching_channel_and_keeps_unchanneled(self, tmp_path: Path):
        root = make_overlay(tmp_path)
        write_pkg_ebuild(root / "dev-util/t3code-bin/t3code-bin-0.0.40.ebuild")
        write_pkg_ebuild(
            root / "dev-util/t3code-nightly-bin/t3code-nightly-bin-0.0.37_pre202608301227.ebuild",
            my_pv="0.0.37-nightly.20260830.1227",
        )
        packages = check_updates.find_packages(root)

        kept = check_updates.filter_packages_by_channel(packages, exclude=["nightly"])

        assert [p.atom for p in kept] == ["dev-util/t3code-bin"]

    def test_no_filters_returns_everything(self, tmp_path: Path):
        root = make_overlay(tmp_path)
        write_pkg_ebuild(root / "dev-util/t3code-bin/t3code-bin-0.0.40.ebuild")
        packages = check_updates.find_packages(root)

        assert check_updates.filter_packages_by_channel(packages) == packages


class TestCheckChannelEbuildNightly:
    def test_nightly_my_pv_derives_nightly_channel(self):
        assert check_updates._derive_channel("0.0.37-nightly.20260830.1227") == "nightly"
        assert check_updates._derive_channel("0.2026.06.03.09.49.stable_00") == "stable"

    def test_nightly_github_release_reports_update(self, tmp_path: Path):
        pkg_path = tmp_path / "dev-util" / "t3code-nightly-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "t3code-nightly-bin-0.0.37_pre202608301227.ebuild"
        write_nightly_ebuild(
            ebuild_path,
            version="0.0.37_pre202608301227",
            my_pv="0.0.37-nightly.20260830.1227",
        )
        ebuild = EbuildName("t3code-nightly-bin", "0.0.37_pre202608301227", ebuild_path)
        (pkg_path / "metadata.xml").write_text(
            "<pkgmetadata><upstream>"
            '<remote-id type="github">pingdotgg/t3code</remote-id>'
            "</upstream></pkgmetadata>"
        )

        class FakeGitHubClient:
            def get_latest_release(self, repo: str, channel: str | None = None):
                assert repo == "pingdotgg/t3code"
                assert channel == "nightly"
                return ReleaseInfo(
                    tag="v0.0.37-nightly.20260831.0101",
                    version="0.0.37-nightly.20260831.0101",
                    url="https://github.com/pingdotgg/t3code/releases/tag/v0.0.37-nightly.20260831.0101",
                )

        status = check_updates.check_channel_ebuild(
            "dev-util", "t3code-nightly-bin", ebuild, pkg_path, FakeGitHubClient()
        )

        assert status.status == "update-available"
        assert status.current_version == "0.0.37_pre202608301227"
        assert status.latest_version == "0.0.37-nightly.20260831.0101"
        assert status.gentoo_version == "0.0.37_pre202608310101"
        assert status.github_repo == "pingdotgg/t3code"
        assert status.my_pv == "0.0.37-nightly.20260830.1227"

    def test_nightly_current_equal_reports_up_to_date(self, tmp_path: Path):
        pkg_path = tmp_path / "dev-util" / "t3code-nightly-bin"
        pkg_path.mkdir(parents=True)
        ebuild_path = pkg_path / "t3code-nightly-bin-0.0.37_pre202608301227.ebuild"
        write_nightly_ebuild(
            ebuild_path,
            version="0.0.37_pre202608301227",
            my_pv="0.0.37-nightly.20260830.1227",
        )
        ebuild = EbuildName("t3code-nightly-bin", "0.0.37_pre202608301227", ebuild_path)
        (pkg_path / "metadata.xml").write_text(
            "<pkgmetadata><upstream>"
            '<remote-id type="github">pingdotgg/t3code</remote-id>'
            "</upstream></pkgmetadata>"
        )

        class FakeGitHubClient:
            def get_latest_release(self, repo: str, channel: str | None = None):
                return ReleaseInfo(
                    tag="v0.0.37-nightly.20260830.1227",
                    version="0.0.37-nightly.20260830.1227",
                    url="https://github.com/pingdotgg/t3code/releases/tag/v0.0.37-nightly.20260830.1227",
                )

        status = check_updates.check_channel_ebuild(
            "dev-util", "t3code-nightly-bin", ebuild, pkg_path, FakeGitHubClient()
        )

        assert status.status == "up-to-date"
        assert status.gentoo_version == "0.0.37_pre202608301227"


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
