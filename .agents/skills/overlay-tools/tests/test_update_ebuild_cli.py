from pathlib import Path

from overlay_tools.cli.update_ebuild import UpdateContext, build_update_plan
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
