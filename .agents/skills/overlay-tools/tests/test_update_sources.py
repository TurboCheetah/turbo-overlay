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
from overlay_tools.core.update_sources.registry import find_source_match


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

    def test_parse_latest_skips_missing_linux_deb_url(self):
        payload = {
            "linux-hayase-6.4.79-linux.deb": None,
            "linux-hayase-6.4.78-linux.deb": "",
        }

        assert parse_latest(payload) is None
