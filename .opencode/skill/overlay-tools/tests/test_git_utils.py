import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from overlay_tools.core.git_utils import (
    git_branch_exists,
    git_checkout_branch,
    git_current_branch,
    git_default_branch,
    git_fetch_branch,
    git_has_changes,
    git_push,
    git_root,
    git_status,
    is_git_repo,
)


class TestIsGitRepo:
    def test_returns_true_for_git_repo(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert is_git_repo(tmp_path) is True

    def test_returns_false_for_non_git_repo(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert is_git_repo(tmp_path) is False


class TestGitCurrentBranch:
    def test_returns_branch_name(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="feature/test\n")
            result = git_current_branch(tmp_path)
            assert result == "feature/test"


class TestGitDefaultBranch:
    def test_returns_master_when_ref_fails(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = git_default_branch(tmp_path)
            assert result == "master"

    def test_returns_main_from_ref(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
            result = git_default_branch(tmp_path)
            assert result == "main"


class TestGitFetchBranch:
    def test_fetch_succeeds(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = git_fetch_branch(tmp_path, "feature/test")
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "fetch", "origin", "feature/test"]

    def test_fetch_fails(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = git_fetch_branch(tmp_path, "nonexistent")
            assert result is False

    def test_fetch_custom_remote(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            git_fetch_branch(tmp_path, "feature/test", remote="upstream")
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "fetch", "upstream", "feature/test"]


class TestGitCheckoutBranch:
    def test_checkout_existing_branch(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            git_checkout_branch("feature/test", tmp_path)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "checkout", "feature/test"]

    def test_create_new_branch(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            git_checkout_branch("feature/new", tmp_path, create=True)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "checkout", "-b", "feature/new"]

    def test_create_from_start_point(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            git_checkout_branch("feature/new", tmp_path, create=True, start_point="master")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "checkout", "-b", "feature/new", "master"]

    def test_track_remote_when_local_missing(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # local branch doesn't exist
                MagicMock(returncode=0),  # fetch succeeds
                MagicMock(returncode=0),  # checkout --track succeeds
            ]
            git_checkout_branch("feature/test", tmp_path, track_remote=True)
            assert mock_run.call_count == 3
            track_cmd = mock_run.call_args_list[2][0][0]
            assert track_cmd == ["git", "checkout", "--track", "origin/feature/test"]

    def test_track_remote_skipped_when_local_exists(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # local branch exists
                MagicMock(returncode=0),  # normal checkout
            ]
            git_checkout_branch("feature/test", tmp_path, track_remote=True)
            assert mock_run.call_count == 2
            checkout_cmd = mock_run.call_args_list[1][0][0]
            assert checkout_cmd == ["git", "checkout", "feature/test"]

    def test_track_remote_fallback_on_failure(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # local branch doesn't exist
                MagicMock(returncode=0),  # fetch succeeds
                MagicMock(returncode=1),  # checkout --track fails
                MagicMock(returncode=0),  # normal checkout succeeds
            ]
            git_checkout_branch("feature/test", tmp_path, track_remote=True)
            assert mock_run.call_count == 4
            final_cmd = mock_run.call_args_list[3][0][0]
            assert final_cmd == ["git", "checkout", "feature/test"]


class TestGitPush:
    def test_push_with_upstream(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            git_push(tmp_path, branch="feature/test")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "push", "-u", "origin", "feature/test"]

    def test_push_without_upstream(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            git_push(tmp_path, branch="feature/test", set_upstream=False)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "push", "origin", "feature/test"]


class TestGitHasChanges:
    def test_has_changes(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="M file.txt\n")
            assert git_has_changes(tmp_path) is True

    def test_no_changes(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            assert git_has_changes(tmp_path) is False


class TestGitBranchExists:
    def test_local_branch_exists(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert git_branch_exists("feature/test", tmp_path) is True

    def test_local_branch_not_exists(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert git_branch_exists("feature/test", tmp_path) is False

    def test_remote_branch_exists(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="abc123 refs/heads/feature/test\n"
            )
            assert git_branch_exists("feature/test", tmp_path, remote=True) is True

    def test_remote_branch_not_exists(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert git_branch_exists("feature/test", tmp_path, remote=True) is False


class TestGitRoot:
    def test_returns_repo_root_path(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="/home/user/repo\n")
            result = git_root(tmp_path)
            assert result == Path("/home/user/repo")

    def test_strips_whitespace(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="  /home/user/repo  \n")
            result = git_root(tmp_path)
            assert result == Path("/home/user/repo")


class TestGitStatus:
    def test_returns_status_output(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="M file.txt\nA new.txt\n")
            result = git_status(tmp_path)
            assert result == "M file.txt\nA new.txt\n"

    def test_returns_empty_for_clean_repo(self, tmp_path):
        with patch("overlay_tools.core.git_utils.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            result = git_status(tmp_path)
            assert result == ""
