import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from overlay_tools.core.gh_utils import (
    PullRequestRef,
    gh_create_pr,
    gh_find_pr_by_head,
    gh_is_available,
    gh_require_available,
)
from overlay_tools.core.errors import ExternalToolMissingError


class TestGhIsAvailable:
    def test_returns_true_when_gh_found(self):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            assert gh_is_available() is True

    def test_returns_false_when_gh_not_found(self):
        with patch("shutil.which", return_value=None):
            assert gh_is_available() is False


class TestGhRequireAvailable:
    def test_raises_when_gh_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(ExternalToolMissingError) as exc_info:
                gh_require_available()
            assert "gh" in str(exc_info.value)

    def test_passes_when_gh_found(self):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            gh_require_available()


class TestGhFindPrByHead:
    def test_returns_none_when_no_pr(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("overlay_tools.core.gh_utils.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="[]")
                result = gh_find_pr_by_head(tmp_path, head="feature/test")
                assert result is None

    def test_returns_pr_ref_when_pr_exists(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("overlay_tools.core.gh_utils.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout=json.dumps(
                        [
                            {
                                "number": 42,
                                "url": "https://github.com/owner/repo/pull/42",
                                "state": "OPEN",
                            }
                        ]
                    )
                )
                result = gh_find_pr_by_head(tmp_path, head="feature/test")
                assert result is not None
                assert result.number == 42
                assert result.state == "OPEN"

    def test_handles_empty_response(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("overlay_tools.core.gh_utils.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="")
                result = gh_find_pr_by_head(tmp_path, head="feature/test")
                assert result is None


class TestGhCreatePr:
    def test_creates_pr_successfully(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("overlay_tools.core.gh_utils.run") as mock_run:
                mock_run.side_effect = [
                    MagicMock(stdout="https://github.com/owner/repo/pull/42\n"),
                    MagicMock(
                        stdout=json.dumps(
                            {
                                "number": 42,
                                "url": "https://github.com/owner/repo/pull/42",
                                "state": "OPEN",
                            }
                        )
                    ),
                ]

                result = gh_create_pr(
                    tmp_path,
                    title="Test PR",
                    body="Test body",
                    head="feature/test",
                    base="main",
                )

                assert result.number == 42
                assert result.url == "https://github.com/owner/repo/pull/42"

    def test_creates_draft_pr(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("overlay_tools.core.gh_utils.run") as mock_run:
                mock_run.side_effect = [
                    MagicMock(stdout="https://github.com/owner/repo/pull/42\n"),
                    MagicMock(
                        stdout=json.dumps(
                            {
                                "number": 42,
                                "url": "https://github.com/owner/repo/pull/42",
                                "state": "DRAFT",
                            }
                        )
                    ),
                ]

                result = gh_create_pr(
                    tmp_path,
                    title="Test PR",
                    body="Test body",
                    head="feature/test",
                    base="main",
                    draft=True,
                )

                assert result.number == 42
                assert result.url == "https://github.com/owner/repo/pull/42"
                assert result.state == "DRAFT"

                create_call = mock_run.call_args_list[0]
                cmd = create_call[0][0]
                assert "--draft" in cmd
