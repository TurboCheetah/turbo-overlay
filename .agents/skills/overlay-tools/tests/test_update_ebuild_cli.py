import argparse
from pathlib import Path

import pytest

from overlay_tools.cli.update_ebuild import (
    AppliedChanges,
    UpdateContext,
    build_update_plan,
    collect_paths_to_stage,
    main,
    should_commit,
)
from overlay_tools.core.ebuilds import EbuildName
from overlay_tools.core.overlay import metadata_cache_path, read_repo_name, select_ebuild_to_drop


class TestReadRepoName:
    def test_reads_profiles_repo_name(self, tmp_path: Path):
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "repo_name").write_text("turbo-overlay\n", encoding="utf-8")

        assert read_repo_name(tmp_path) == "turbo-overlay"

    def test_returns_none_when_repo_name_missing(self, tmp_path: Path):
        assert read_repo_name(tmp_path) is None


class TestMetadataCachePath:
    def test_builds_expected_path(self, tmp_path: Path):
        path = metadata_cache_path(tmp_path, "media-video", "hayase-bin", "6.4.56")

        assert path == tmp_path / "metadata" / "md5-cache" / "media-video" / "hayase-bin-6.4.56"


class TestRetentionPolicy:
    def test_keeps_all_versions_when_fewer_than_four_non_live(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "1.1.0", Path("pkg-1.1.0.ebuild")),
            EbuildName("pkg", "1.2.0", Path("pkg-1.2.0.ebuild")),
        ]

        assert select_ebuild_to_drop(ebuilds) is None

    def test_drops_oldest_when_four_non_live_versions_exist(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "1.1.0", Path("pkg-1.1.0.ebuild")),
            EbuildName("pkg", "1.2.0", Path("pkg-1.2.0.ebuild")),
            EbuildName("pkg", "1.3.0", Path("pkg-1.3.0.ebuild")),
        ]

        dropped = select_ebuild_to_drop(ebuilds)

        assert dropped is not None
        assert dropped.pv == "1.0.0"

    def test_ignores_live_ebuild_for_drop_threshold(self):
        ebuilds = [
            EbuildName("pkg", "1.0.0", Path("pkg-1.0.0.ebuild")),
            EbuildName("pkg", "1.1.0", Path("pkg-1.1.0.ebuild")),
            EbuildName("pkg", "1.2.0", Path("pkg-1.2.0.ebuild")),
            EbuildName("pkg", "9999", Path("pkg-9999.ebuild")),
        ]

        assert select_ebuild_to_drop(ebuilds) is None


class TestBuildUpdatePlan:
    def test_commit_message_and_drop_selection_follow_policy(self, tmp_path: Path):
        context = UpdateContext(
            category="media-video",
            name="hayase-bin",
            pkg_path=tmp_path,
            repo_root=tmp_path,
            is_git=False,
            latest=EbuildName("hayase-bin", "6.4.56", tmp_path / "hayase-bin-6.4.56.ebuild"),
            ebuilds=[
                EbuildName("hayase-bin", "6.4.53", tmp_path / "hayase-bin-6.4.53.ebuild"),
                EbuildName("hayase-bin", "6.4.54", tmp_path / "hayase-bin-6.4.54.ebuild"),
                EbuildName("hayase-bin", "6.4.55", tmp_path / "hayase-bin-6.4.55.ebuild"),
                EbuildName("hayase-bin", "6.4.56", tmp_path / "hayase-bin-6.4.56.ebuild"),
            ],
            base_branch="master",
            feature_branch="update/media-video-hayase-bin",
            original_branch=None,
        )

        plan = build_update_plan(context, "6.4.57", keep_old=False)

        assert plan.drop_ebuild is not None
        assert plan.drop_ebuild.pv == "6.4.53"
        assert plan.commit_message == "media-video/hayase-bin: add 6.4.57, drop 6.4.53"

    def test_keep_old_disables_drop(self, tmp_path: Path):
        context = UpdateContext(
            category="media-video",
            name="hayase-bin",
            pkg_path=tmp_path,
            repo_root=tmp_path,
            is_git=False,
            latest=EbuildName("hayase-bin", "6.4.56", tmp_path / "hayase-bin-6.4.56.ebuild"),
            ebuilds=[
                EbuildName("hayase-bin", "6.4.53", tmp_path / "hayase-bin-6.4.53.ebuild"),
                EbuildName("hayase-bin", "6.4.54", tmp_path / "hayase-bin-6.4.54.ebuild"),
                EbuildName("hayase-bin", "6.4.55", tmp_path / "hayase-bin-6.4.55.ebuild"),
                EbuildName("hayase-bin", "6.4.56", tmp_path / "hayase-bin-6.4.56.ebuild"),
            ],
            base_branch="master",
            feature_branch="update/media-video-hayase-bin",
            original_branch=None,
        )

        plan = build_update_plan(context, "6.4.57", keep_old=True)

        assert plan.drop_ebuild is None
        assert plan.commit_message == "media-video/hayase-bin: add 6.4.57"


def make_context(tmp_path: Path) -> UpdateContext:
    pkg_path = tmp_path / "dev-util" / "t3code-bin"
    pkg_path.mkdir(parents=True)

    return UpdateContext(
        category="dev-util",
        name="t3code-bin",
        pkg_path=pkg_path,
        repo_root=tmp_path,
        is_git=True,
        latest=EbuildName("t3code-bin", "0.0.9", pkg_path / "t3code-bin-0.0.9.ebuild"),
        ebuilds=[
            EbuildName("t3code-bin", "0.0.4", pkg_path / "t3code-bin-0.0.4.ebuild"),
            EbuildName("t3code-bin", "0.0.7", pkg_path / "t3code-bin-0.0.7.ebuild"),
            EbuildName("t3code-bin", "0.0.8", pkg_path / "t3code-bin-0.0.8.ebuild"),
            EbuildName("t3code-bin", "0.0.9", pkg_path / "t3code-bin-0.0.9.ebuild"),
        ],
        base_branch="master",
        feature_branch="update/dev-util-t3code-bin",
        original_branch="master",
    )


class TestCommitFlowHelpers:
    def test_collect_paths_to_stage_skips_missing_deleted_cache(self, tmp_path: Path):
        context = make_context(tmp_path)
        plan = build_update_plan(context, "0.0.11", keep_old=False)
        manifest_path = context.pkg_path / "Manifest"
        manifest_path.write_text("", encoding="utf-8")
        plan.new_path.write_text("", encoding="utf-8")

        paths = collect_paths_to_stage(
            plan,
            AppliedChanges(
                deleted_ebuild_path=plan.drop_ebuild.path if plan.drop_ebuild else None,
                deleted_cache_path=None,
            ),
        )

        assert paths == [
            plan.new_path,
            manifest_path,
            plan.drop_ebuild.path,
        ]
        assert plan.drop_cache_path not in paths

    def test_collect_paths_to_stage_includes_deleted_cache_when_removed(self, tmp_path: Path):
        context = make_context(tmp_path)
        plan = build_update_plan(context, "0.0.11", keep_old=False)
        manifest_path = context.pkg_path / "Manifest"
        manifest_path.write_text("", encoding="utf-8")
        plan.new_path.write_text("", encoding="utf-8")
        plan.new_cache_path.parent.mkdir(parents=True, exist_ok=True)
        plan.new_cache_path.write_text("", encoding="utf-8")

        paths = collect_paths_to_stage(
            plan,
            AppliedChanges(
                deleted_ebuild_path=plan.drop_ebuild.path if plan.drop_ebuild else None,
                deleted_cache_path=plan.drop_cache_path,
            ),
        )

        assert paths == [
            plan.new_path,
            manifest_path,
            plan.new_cache_path,
            plan.drop_ebuild.path,
            plan.drop_cache_path,
        ]

    def test_should_commit_returns_true_for_yes_flag(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        class StubLog:
            def info(self, message: str) -> None:
                pass

        assert should_commit(
            log=StubLog(),
            args=argparse.Namespace(yes=True, pr=False),
        )

    def test_should_commit_skips_noninteractive_without_yes(self, monkeypatch):
        messages: list[str] = []

        class Log:
            def info(self, message: str) -> None:
                messages.append(message)

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        assert not should_commit(
            log=Log(),
            args=argparse.Namespace(yes=False, pr=False),
        )
        assert messages == ["Non-interactive mode detected, skipping commit (use -y to commit)"]


class TestPrGuardrails:
    def test_pr_rejects_skip_git(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--pr", "--skip-git", "--version", "1.0.0", "media-video/hayase-bin"])

        assert excinfo.value.code == 2

    def test_pr_requires_git_repository(self, monkeypatch, tmp_path: Path):
        pkg_path = tmp_path / "media-video" / "hayase-bin"
        pkg_path.mkdir(parents=True)
        (pkg_path / "hayase-bin-1.0.0.ebuild").write_text("", encoding="utf-8")

        monkeypatch.setattr(
            "overlay_tools.cli.update_ebuild.is_git_repo",
            lambda path: False,
        )

        result = main(["--pr", "--version", "1.0.1", str(pkg_path)])

        assert result == 1
