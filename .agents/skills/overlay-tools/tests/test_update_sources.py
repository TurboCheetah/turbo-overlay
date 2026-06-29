import httpx

import overlay_tools.core.update_sources.hayase as hayase_module
from overlay_tools.core.update_sources.base import (
    PackageSourceContext,
    SourceMatch,
    SourceRelease,
)
from overlay_tools.core.update_sources.hayase import (
    HAYASE_API_URL,
    HayaseUpdateSource,
    parse_latest,
)
from overlay_tools.core.update_sources.registry import DEFAULT_UPDATE_SOURCES, find_source_match
from overlay_tools.core.update_sources.warp import WARP_CHANGELOG_URL, WarpUpdateSource


class DummySource:
    name = "dummy"

    def match(self, context: PackageSourceContext) -> SourceMatch | None:
        if context.name == "dummy-bin":
            return SourceMatch(source_name=self.name, source_url="https://example.invalid/latest")
        return None

    def latest_release(self, match: SourceMatch) -> SourceRelease | None:
        return SourceRelease(version="1.2.3", url=match.source_url)


def test_find_source_match_returns_first_matching_source():
    context = PackageSourceContext(
        category="dev-util",
        name="dummy-bin",
        src_uri=None,
        homepage=None,
    )

    result = find_source_match(context, sources=(DummySource(),))

    assert result is not None
    source, match = result
    assert source.name == "dummy"
    assert match.source_name == "dummy"
    assert match.source_url == "https://example.invalid/latest"


def test_find_source_match_returns_none_when_no_plugin_matches():
    context = PackageSourceContext(
        category="dev-util",
        name="other-bin",
        src_uri=None,
        homepage=None,
    )

    assert find_source_match(context, sources=(DummySource(),)) is None


class TestHayaseUpdateSource:
    def test_matches_hayase_host_from_src_uri(self):
        source = HayaseUpdateSource()
        context = PackageSourceContext(
            category="media-video",
            name="hayase-bin",
            src_uri="https://api.hayase.watch/files/linux-hayase-${PV}-linux.deb",
            homepage=None,
        )

        match = source.match(context)

        assert match is not None
        assert match.source_name == "hayase"
        assert match.source_url == HAYASE_API_URL

    def test_matches_hayase_host_from_homepage(self):
        source = HayaseUpdateSource()
        context = PackageSourceContext(
            category="media-video",
            name="not-obvious",
            src_uri=None,
            homepage="https://hayase.watch/",
        )

        match = source.match(context)

        assert match is not None
        assert match.source_url == HAYASE_API_URL

    def test_does_not_match_hayase_token_outside_hostname(self):
        source = HayaseUpdateSource()
        context = PackageSourceContext(
            category="media-video",
            name="not-obvious",
            src_uri="https://example.invalid/download?mirror=hayase.watch",
            homepage="https://not-hayase.watch.example/",
        )

        assert source.match(context) is None

    def test_matches_vendor_name_fallback_without_substring_false_positive(self):
        source = HayaseUpdateSource()
        hayase = PackageSourceContext("media-video", "hayase-bin", None, None)
        not_hayase = PackageSourceContext("media-video", "hayaseish-bin", None, None)

        assert source.match(hayase) is not None
        assert source.match(not_hayase) is None

    def test_parse_latest_extracts_linux_deb_version(self):
        payload = {
            "android-6.4.52.apk": "https://api.hayase.watch/files/android-6.4.52.apk",
            "linux-hayase-6.4.79-linux.AppImage": "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.AppImage",
            "linux-hayase-6.4.79-linux.deb": "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb",
        }

        release = parse_latest(payload)

        assert release is not None
        assert release.version == "6.4.79"
        assert release.url == "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb"

    def test_parse_latest_returns_highest_linux_deb_version(self):
        payload = {
            "linux-hayase-6.4.79-linux.deb": "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb",
            "linux-hayase-6.4.80-linux.deb": "https://api.hayase.watch/files/linux-hayase-6.4.80-linux.deb",
        }

        release = parse_latest(payload)

        assert release == SourceRelease(
            version="6.4.80",
            url="https://api.hayase.watch/files/linux-hayase-6.4.80-linux.deb",
        )

    def test_parse_latest_skips_missing_linux_deb_url(self):
        payload = {
            "linux-hayase-6.4.79-linux.deb": None,
            "linux-hayase-6.4.78-linux.deb": "",
        }

        assert parse_latest(payload) is None

    def test_latest_release_fetches_and_parses_expected_url(self, monkeypatch):
        source = HayaseUpdateSource()
        match = SourceMatch(source_name="hayase", source_url=HAYASE_API_URL)
        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "linux-hayase-6.4.79-linux.deb": (
                        "https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb"
                    )
                }

        def fake_get(url: str, *, timeout: int, follow_redirects: bool):
            calls.append((url, timeout, follow_redirects))
            return Response()

        monkeypatch.setattr(hayase_module.httpx, "get", fake_get)

        release = source.latest_release(match)

        assert calls == [(HAYASE_API_URL, 10, True)]
        assert release == SourceRelease(
            version="6.4.79",
            url="https://api.hayase.watch/files/linux-hayase-6.4.79-linux.deb",
        )

    def test_latest_release_returns_none_for_http_errors(self, monkeypatch):
        source = HayaseUpdateSource()
        match = SourceMatch(source_name="hayase", source_url=HAYASE_API_URL)

        def fake_get(url: str, *, timeout: int, follow_redirects: bool):
            raise httpx.HTTPError("boom")

        monkeypatch.setattr(hayase_module.httpx, "get", fake_get)

        assert source.latest_release(match) is None

    def test_latest_release_returns_none_for_invalid_json(self, monkeypatch):
        source = HayaseUpdateSource()
        match = SourceMatch(source_name="hayase", source_url=HAYASE_API_URL)

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("not json")

        monkeypatch.setattr(
            hayase_module.httpx,
            "get",
            lambda url, *, timeout, follow_redirects: Response(),
        )

        assert source.latest_release(match) is None

    def test_latest_release_returns_none_for_non_dict_payload(self, monkeypatch):
        source = HayaseUpdateSource()
        match = SourceMatch(source_name="hayase", source_url=HAYASE_API_URL)

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return ["not", "a", "dict"]

        monkeypatch.setattr(
            hayase_module.httpx,
            "get",
            lambda url, *, timeout, follow_redirects: Response(),
        )

        assert source.latest_release(match) is None


class TestWarpUpdateSource:
    def test_matches_warp_host_from_homepage(self):
        source = WarpUpdateSource()
        context = PackageSourceContext(
            category="x11-terms",
            name="warp-bin",
            src_uri=None,
            homepage="https://www.warp.dev/download",
        )

        match = source.match(context)

        assert match is not None
        assert match.source_name == "warp"
        assert match.source_url == WARP_CHANGELOG_URL
        assert match.fallback_to_github is True

    def test_matches_vendor_name_fallback_without_substring_false_positive(self):
        source = WarpUpdateSource()
        warp = PackageSourceContext("x11-terms", "warp-bin", None, None)
        not_warp = PackageSourceContext("x11-terms", "timewarp-bin", None, None)

        assert source.match(warp) is not None
        assert source.match(not_warp) is None

    def test_latest_release_returns_none_to_preserve_manual_check_fallback(self):
        source = WarpUpdateSource()
        match = SourceMatch(source_name="warp", source_url=WARP_CHANGELOG_URL)

        assert source.latest_release(match) is None


def test_default_registry_includes_hayase_and_warp_sources():
    names = {source.name for source in DEFAULT_UPDATE_SOURCES}

    assert names == {"hayase", "warp"}
