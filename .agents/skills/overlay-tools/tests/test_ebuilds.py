import pytest
from pathlib import Path
import tempfile

from overlay_tools.core.ebuilds import (
    EbuildName,
    find_ebuilds,
    parse_ebuild_filename,
    read_ebuild_vars,
    select_latest_ebuild,
    update_ebuild_var,
)
from overlay_tools.core.errors import EbuildParseError


class TestParseEbuildFilename:
    def test_simple_name(self):
        result = parse_ebuild_filename(Path("foo-1.2.3.ebuild"))
        assert result.pn == "foo"
        assert result.pv == "1.2.3"

    def test_name_with_dash(self):
        result = parse_ebuild_filename(Path("foo-bar-1.2.3.ebuild"))
        assert result.pn == "foo-bar"
        assert result.pv == "1.2.3"

    def test_version_with_suffix(self):
        result = parse_ebuild_filename(Path("pkg-1.2.3_p1.ebuild"))
        assert result.pn == "pkg"
        assert result.pv == "1.2.3_p1"

    def test_version_with_revision(self):
        result = parse_ebuild_filename(Path("pkg-1.2.3-r1.ebuild"))
        assert result.pn == "pkg"
        assert result.pv == "1.2.3-r1"

    def test_bin_suffix(self):
        result = parse_ebuild_filename(Path("hayase-bin-6.4.48.ebuild"))
        assert result.pn == "hayase-bin"
        assert result.pv == "6.4.48"

    def test_invalid_filename(self):
        with pytest.raises(EbuildParseError):
            parse_ebuild_filename(Path("not-an-ebuild.txt"))

    def test_no_version(self):
        with pytest.raises(EbuildParseError):
            parse_ebuild_filename(Path("pkg.ebuild"))


class TestReadEbuildVars:
    def test_read_simple_vars(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ebuild", delete=False) as f:
            f.write("EAPI=8\n")
            f.write('DESCRIPTION="A test package"\n')
            f.write("HOMEPAGE='https://example.com'\n")
            f.write('MY_PV="1.2.3"\n')
            f.flush()

            result = read_ebuild_vars(Path(f.name))
            assert result["EAPI"] == "8"
            assert result["DESCRIPTION"] == "A test package"
            assert result["HOMEPAGE"] == "https://example.com"
            assert result["MY_PV"] == "1.2.3"

            Path(f.name).unlink()

    def test_read_specific_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ebuild", delete=False) as f:
            f.write("EAPI=8\n")
            f.write('DESCRIPTION="A test package"\n')
            f.write('HOMEPAGE="https://example.com"\n')
            f.flush()

            result = read_ebuild_vars(Path(f.name), {"HOMEPAGE"})
            assert "HOMEPAGE" in result
            assert "DESCRIPTION" not in result

            Path(f.name).unlink()


class TestUpdateEbuildVar:
    def test_update_existing_var(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ebuild", delete=False) as f:
            f.write("EAPI=8\n")
            f.write('MY_PV="1.2.3"\n')
            f.write('DESCRIPTION="Test"\n')
            f.flush()
            path = Path(f.name)

            result = update_ebuild_var(path, "MY_PV", "2.0.0")
            assert result is True

            content = path.read_text()
            assert 'MY_PV="2.0.0"' in content
            assert "EAPI=8" in content

            path.unlink()

    def test_update_nonexistent_var(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ebuild", delete=False) as f:
            f.write("EAPI=8\n")
            f.write('DESCRIPTION="Test"\n')
            f.flush()
            path = Path(f.name)

            result = update_ebuild_var(path, "MY_PV", "2.0.0")
            assert result is False

            path.unlink()


class TestSelectLatestEbuild:
    def test_select_latest(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "2.0.0", Path("pkg-2.0.0.ebuild")),
            EbuildName("pkg", "1.5.0", Path("pkg-1.5.0.ebuild")),
        ]
        result = select_latest_ebuild(ebuilds)
        assert result is not None
        assert result.pv == "2.0.0"

    def test_excludes_live_by_default(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "9999", Path("pkg-9999.ebuild")),
        ]
        result = select_latest_ebuild(ebuilds)
        assert result is not None
        assert result.pv == "1.0.0"

    def test_includes_live_when_requested(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "9999", Path("pkg-9999.ebuild")),
        ]
        result = select_latest_ebuild(ebuilds, exclude_live=False)
        assert result is not None
        assert result.pv == "9999"

    def test_empty_list(self):
        result = select_latest_ebuild([])
        assert result is None

    def test_only_live_excluded(self):
        ebuilds = [
            EbuildName("pkg", "9999", Path("pkg-9999.ebuild")),
        ]
        result = select_latest_ebuild(ebuilds)
        assert result is None
