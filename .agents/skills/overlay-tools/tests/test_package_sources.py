from overlay_tools.core.package_sources import get_custom_url


class TestGetCustomUrl:
    def test_matches_host_from_src_uri(self):
        assert (
            get_custom_url("hayase-bin", "https://hayase.watch/download", None)
            == "https://hayase.watch/"
        )

    def test_matches_host_from_homepage(self):
        assert (
            get_custom_url("warp-bin", None, "https://www.warp.dev/download")
            == "https://www.warp.dev/changelog"
        )

    def test_matches_vendor_name_fallback(self):
        assert get_custom_url("hayase-bin", None, None) == "https://hayase.watch/"

    def test_returns_none_for_unknown_package(self):
        assert get_custom_url("unknown", None, None) is None
