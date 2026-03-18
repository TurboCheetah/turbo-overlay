import pytest

from overlay_tools.core.report import (
    PackageStatus,
    StatusSummary,
    render_terminal_report,
    sort_packages,
    summarize_packages,
)


def _sample_packages() -> list[PackageStatus]:
    return [
        PackageStatus(
            category="net-im",
            name="goofcord",
            current_version="1.0.0",
            latest_version="1.1.0",
            github_repo="owner/goofcord",
            custom_url=None,
            status="update-available",
        ),
        PackageStatus(
            category="x11-terms",
            name="warp-bin",
            current_version="0.1.0",
            latest_version=None,
            github_repo=None,
            custom_url="https://www.warp.dev/changelog",
            status="manual-check",
        ),
        PackageStatus(
            category="media-video",
            name="hayase-bin",
            current_version="6.4.56",
            latest_version="6.4.56",
            github_repo="owner/hayase",
            custom_url=None,
            status="up-to-date",
        ),
        PackageStatus(
            category="games-util",
            name="vkbasalt",
            current_version="3.0.0",
            latest_version=None,
            github_repo=None,
            custom_url=None,
            status="error",
            error_message="boom",
        ),
    ]


class TestSortPackages:
    def test_orders_by_status_then_atom(self):
        sorted_packages = sort_packages(_sample_packages())

        assert [package.status for package in sorted_packages] == [
            "update-available",
            "manual-check",
            "error",
            "up-to-date",
        ]


class TestSummarizePackages:
    def test_counts_status_groups(self):
        summary = summarize_packages(_sample_packages())

        assert summary == StatusSummary(updates=1, up_to_date=1, manual=1, unknown=0, errors=1)
        assert summary.checked == 2


    def test_counts_unknown_statuses_separately(self):
        summary = summarize_packages(
            _sample_packages()
            + [
                PackageStatus(
                    category="net-im",
                    name="mystery",
                    current_version="0.0.1",
                    latest_version=None,
                    github_repo=None,
                    custom_url=None,
                    status="unknown",
                )
            ]
        )

        assert summary.unknown == 1
        assert summary.errors == 1


class TestRenderTerminalReport:
    def test_plain_fallback_outputs_summary(self, monkeypatch, capsys):
        monkeypatch.setattr("overlay_tools.core.report._render_rich", lambda packages: (_ for _ in ()).throw(ImportError))

        render_terminal_report(_sample_packages())
        output = capsys.readouterr().out

        assert "turbo-overlay Update Check" in output
        assert "1 updates available" in output
        assert "1 need manual check" in output

    def test_plain_fallback_reports_unknown_statuses(self, monkeypatch, capsys):
        monkeypatch.setattr("overlay_tools.core.report._render_rich", lambda packages: (_ for _ in ()).throw(ImportError))

        render_terminal_report(
            _sample_packages()
            + [
                PackageStatus(
                    category="net-im",
                    name="mystery",
                    current_version="0.0.1",
                    latest_version=None,
                    github_repo=None,
                    custom_url=None,
                    status="unknown",
                )
            ]
        )
        output = capsys.readouterr().out

        assert "1 unknown status" in output

    def test_rich_render_contains_key_content(self, capsys):
        pytest.importorskip("rich")
        render_terminal_report(_sample_packages())
        output = capsys.readouterr().out

        assert "turbo-overlay" in output
        assert "net-im/goofcord" in output
        assert "Summary" in output
