import pytest

from overlay_tools.core.errors import VersionError
from overlay_tools.core.versions import (
    GentooVersion,
    compare_versions,
    normalize_gentoo_version,
    parse_gentoo_version,
    upstream_to_gentoo,
)


class TestParseGentooVersion:
    def test_simple_version(self):
        v = parse_gentoo_version("1.2.3")
        assert v.base == "1.2.3"
        assert v.letter is None
        assert v.suffix_type is None
        assert v.revision is None

    def test_version_with_letter(self):
        v = parse_gentoo_version("1.2.3a")
        assert v.base == "1.2.3"
        assert v.letter == "a"

    def test_version_with_patch_suffix(self):
        v = parse_gentoo_version("1.2.3_p1")
        assert v.base == "1.2.3"
        assert v.suffix_type == "p"
        assert v.suffix_num == 1

    def test_version_with_pre_suffix(self):
        v = parse_gentoo_version("1.2.3_pre")
        assert v.base == "1.2.3"
        assert v.suffix_type == "pre"
        assert v.suffix_num is None

    def test_version_with_revision(self):
        v = parse_gentoo_version("1.2.3-r2")
        assert v.base == "1.2.3"
        assert v.revision == 2

    def test_complex_version(self):
        v = parse_gentoo_version("0.2025.01.07.08.13_p01")
        assert v.base == "0.2025.01.07.08.13"
        assert v.suffix_type == "p"
        assert v.suffix_num == 1

    def test_invalid_version(self):
        with pytest.raises(VersionError):
            parse_gentoo_version("invalid")

    def test_version_str(self):
        v = parse_gentoo_version("1.2.3_p1-r2")
        assert str(v) == "1.2.3_p1-r2"


class TestNormalizeGentooVersion:
    def test_valid_version_passthrough(self):
        assert normalize_gentoo_version("1.2.3") == "1.2.3"
        assert normalize_gentoo_version("1.2.3_p1") == "1.2.3_p1"

    def test_strips_v_prefix(self):
        assert normalize_gentoo_version("v1.2.3") == "1.2.3"
        assert normalize_gentoo_version("V1.2.3") == "1.2.3"

    def test_stable_suffix_conversion_lenient(self):
        result = normalize_gentoo_version("1.2.3.stable_04", lenient=True)
        assert result == "1.2.3_p04"

    def test_invalid_without_lenient(self):
        with pytest.raises(VersionError):
            normalize_gentoo_version("1.2.3.stable_04")

    def test_lenient_allows_nonstandard(self):
        result = normalize_gentoo_version("1.2.3.stable_04", lenient=True)
        assert "_p" in result


class TestCompareVersions:
    def test_equal_versions(self):
        assert compare_versions("1.2.3", "1.2.3") == 0

    def test_greater_version(self):
        assert compare_versions("1.2.4", "1.2.3") == 1

    def test_lesser_version(self):
        assert compare_versions("1.2.3", "1.2.4") == -1

    def test_patch_suffix_comparison(self):
        assert compare_versions("1.2.3_p2", "1.2.3_p1") == 1
        assert compare_versions("1.2.3_p1", "1.2.3") == 1

    def test_pre_suffix_less_than_release(self):
        assert compare_versions("1.2.3_pre", "1.2.3") == -1

    def test_different_segment_counts(self):
        assert compare_versions("1.2.3.4", "1.2.3") == 1


class TestUpstreamToGentoo:
    def test_strips_v_prefix(self):
        assert upstream_to_gentoo("v1.2.3") == "1.2.3"

    def test_strips_release_prefix(self):
        assert upstream_to_gentoo("release-1.2.3") == "1.2.3"

    def test_converts_stable_suffix(self):
        result = upstream_to_gentoo("1.2.3.stable_04")
        assert result == "1.2.3_p04"


class TestGentooVersionToPep440:
    def test_alpha(self):
        v = parse_gentoo_version("1.2.3_alpha1")
        assert v.to_pep440() == "1.2.3a1"

    def test_beta(self):
        v = parse_gentoo_version("1.2.3_beta2")
        assert v.to_pep440() == "1.2.3b2"

    def test_rc(self):
        v = parse_gentoo_version("1.2.3_rc1")
        assert v.to_pep440() == "1.2.3rc1"

    def test_pre(self):
        v = parse_gentoo_version("1.2.3_pre")
        assert v.to_pep440() == "1.2.3.dev0"

    def test_patch(self):
        v = parse_gentoo_version("1.2.3_p1")
        assert v.to_pep440() == "1.2.3.post1"
