from overlay_tools.core.package_sources import get_custom_url, parse_hayase_latest


class TestHayaseLatest:
    def test_extracts_latest_linux_deb_version(self):
        payload = {
            "android-6.4.52.apk": "https://api.hayase.watch/files/android-6.4.52.apk",
            "linux-hayase-6.4.79-linux.AppImage": "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.AppImage",
            "linux-hayase-6.4.79-linux.deb": "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb",
            "latest-linux.yml": "https://api.hayase.watch/files/latest-linux.yml",
        }

        release = parse_hayase_latest(payload)

        assert release is not None
        assert release.version == "6.4.79"
        assert release.url == "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb"


class TestGetCustomUrl:
    def test_matches_host_from_src_uri(self):
        assert (
            get_custom_url("hayase-bin", "https://hayase.watch/download", None)
            == "https://api.hayase.watch/latest"
        )

    def test_matches_host_from_homepage(self):
        assert (
            get_custom_url("warp-bin", None, "https://www.warp.dev/download")
            == "https://docs.warp.dev/changelog"
        )

    def test_matches_vendor_name_fallback(self):
        assert get_custom_url("hayase-bin", None, None) == "https://api.hayase.watch/latest"

    def test_returns_none_for_unknown_package(self):
        assert get_custom_url("unknown", None, None) is None

    def test_does_not_match_vendor_substrings(self):
        assert get_custom_url("timewarp-bin", None, None) is None
