"""Container runner validation and command construction tests (no Docker required)."""

import subprocess
from pathlib import Path

import pytest

from overlay_tools.cli import test_ebuild
from overlay_tools.core.errors import ExternalToolMissingError

EBUILD = "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild"
RUN_EBUILD = test_ebuild.TOOLS_ROOT / "docker/run-ebuild"


def overlay(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/repo_name").write_text("turbo-overlay\n")
    pkg = tmp_path / "dev-util/t3code-nightly-bin"
    pkg.mkdir(parents=True)
    (pkg / "t3code-nightly-bin-1.ebuild").write_text("EAPI=8\n")
    return tmp_path


def fake_docker(monkeypatch: pytest.MonkeyPatch, **returncodes: int) -> list[list[str]]:
    """Record docker calls; returncodes are keyed by subcommand (build, image, run)."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncodes.get(cmd[1], 0), "", "")

    monkeypatch.setattr(test_ebuild, "require_tool", lambda *args: Path("/usr/bin/docker"))
    monkeypatch.setattr(test_ebuild, "run", fake_run)
    return calls


def test_validates_exact_ebuild_path(tmp_path: Path) -> None:
    root = overlay(tmp_path)
    pkg = root / "dev-util/t3code-nightly-bin"
    for name in (
        "different-1.ebuild",
        "t3code-nightly-bin-extra-1.ebuild",
        "t3code-nightly-bin-x.ebuild",
    ):
        (pkg / name).write_text("EAPI=8\n")
    test_ebuild.validate_ebuild(root, EBUILD)
    for bad in (
        "dev-util/t3code-nightly-bin",
        "../outside.ebuild",
        "/etc/passwd",
        "dev-util/../t3code-nightly-bin-1.ebuild",
        "dev-util/t3code-nightly-bin/different-1.ebuild",
        "dev-util/t3code-nightly-bin/t3code-nightly-bin-extra-1.ebuild",
        "dev-util/t3code-nightly-bin/t3code-nightly-bin-x.ebuild",
    ):
        with pytest.raises(ValueError):
            test_ebuild.validate_ebuild(root, bad)


def test_refuses_symlink_outside_overlay(tmp_path: Path) -> None:
    root = overlay(tmp_path)
    (root / "dev-util/t3code-nightly-bin/t3code-nightly-bin-2.ebuild").symlink_to(
        root.parent / "external.ebuild"
    )
    (root.parent / "external.ebuild").write_text("EAPI=8\n")
    with pytest.raises(ValueError):
        test_ebuild.validate_ebuild(root, "dev-util/t3code-nightly-bin/t3code-nightly-bin-2.ebuild")


@pytest.mark.parametrize("dirname", ["a,b", 'a"b'])
def test_refuses_mount_path_breaking_mount_spec(dirname: str) -> None:
    with pytest.raises(ValueError, match="comma or double quote"):
        test_ebuild.bind_mount(Path("/tmp") / dirname, "/dst")


def test_refuses_non_overlay(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a Gentoo overlay"):
        test_ebuild.validate_overlay(tmp_path)


def test_build_and_run_mount_readonly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch)
    argv = ["--overlay-path", str(root), "--build", "--expect", "usr/bin/t3code", EBUILD]
    assert test_ebuild.main(argv) == 0
    build, docker_run = calls
    assert build[:4] == ["docker", "build", "--pull", "--no-cache"]
    assert build[build.index("--platform") + 1] == "linux/amd64"
    assert build[build.index("-t") + 1] == test_ebuild.DOCKER_TAG
    assert docker_run[:5] == ["docker", "run", "--rm", "--platform", "linux/amd64"]
    script = test_ebuild.TOOLS_ROOT / "docker/run-ebuild"
    assert docker_run[5:11] == [
        "--mount",
        f"type=bind,src={root},dst=/var/db/repos/turbo-overlay,readonly",
        "--mount",
        f"type=bind,src={script},dst=/usr/local/bin/run-ebuild,readonly",
        "--env",
        "OVERLAY_REPO=/var/db/repos/turbo-overlay",
    ]
    assert docker_run[-3:] == [test_ebuild.DOCKER_TAG, EBUILD, "usr/bin/t3code"]


def test_refuses_invalid_expected_path_before_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main(["--overlay-path", str(root), "--expect", "../../etc/passwd", EBUILD])
    assert exc.value.code == 2
    assert calls == []


def test_build_failure_exits_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch, build=1)
    assert test_ebuild.main(["--overlay-path", str(root), "--build", EBUILD]) == 2
    assert len(calls) == 1


def test_missing_image_exits_before_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch, image=1)
    assert test_ebuild.main(["--overlay-path", str(root), EBUILD]) == 2
    assert calls == [["docker", "image", "inspect", test_ebuild.DOCKER_TAG]]
    output = capsys.readouterr()
    assert "rerun with --build" in output.out + output.err


def test_missing_docker_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch)

    def missing(*args: object) -> Path:
        raise ExternalToolMissingError("docker")

    monkeypatch.setattr(test_ebuild, "require_tool", missing)
    assert test_ebuild.main(["--overlay-path", str(root), "--build", EBUILD]) == 2
    assert calls == []
    output = capsys.readouterr()
    assert "Required tool not found: docker" in output.out + output.err
    assert "usage:" not in output.out + output.err


def test_returns_container_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch, run=17)
    assert test_ebuild.main(["--overlay-path", str(root), EBUILD]) == 17
    assert [call[1] for call in calls] == ["image", "run"]


@pytest.mark.parametrize(
    ("args", "env"),
    [([], {"OVERLAY_REPO": "/repo"}), ([EBUILD], {})],
)
def test_run_ebuild_argument_errors_exit_2(args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(
        ["bash", str(RUN_EBUILD), *args], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 2


def run_ebuild_fn(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    script = f'source "{RUN_EBUILD}"; "$@"'
    return subprocess.run(
        ["bash", "-c", script, "run-ebuild", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(
    ("path", "pf"),
    [
        (EBUILD, "t3code-nightly-bin-1"),
        ("net-im/goofcord/goofcord-1.2.3-r1.ebuild", "goofcord-1.2.3-r1"),
    ],
)
def test_run_ebuild_image_dir(path: str, pf: str) -> None:
    result = run_ebuild_fn("image_dir_for", path, env={"PORTAGE_TMPDIR": "/tmp/x"})
    category = path.split("/")[0]
    assert result.stdout == f"/tmp/x/portage/{category}/{pf}/image\n"
    for bad in ("net-im/goofcord/other-1.ebuild", "net-im/goofcord/goofcord-extra-1.ebuild"):
        assert run_ebuild_fn("image_dir_for", bad).returncode == 1


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        (EBUILD, True),
        ("net-im/goofcord/goofcord-1.2.3-r1.ebuild", True),
        ("net-im/../goofcord-1.ebuild", False),
        ("./goofcord/goofcord-1.ebuild", False),
        ("net-im/goofcord", False),
        ("/net-im/goofcord/goofcord-1.ebuild", False),
    ],
)
def test_run_ebuild_valid_ebuild_path(path: str, valid: bool) -> None:
    assert (run_ebuild_fn("valid_ebuild_path", path).returncode == 0) is valid


def test_run_ebuild_staged_path_checks(tmp_path: Path) -> None:
    image = tmp_path / "image"
    (image / "usr/bin").mkdir(parents=True)
    (image / "usr/bin/real").write_text("")
    (image / "usr/bin/launcher").symlink_to("/opt/app/app")
    (image / "escape").symlink_to("/etc")

    def exists(path: str) -> bool:
        return run_ebuild_fn("staged_path_exists", str(image), path).returncode == 0

    assert exists("usr/bin/real")
    assert exists("usr/bin/launcher")
    assert not exists("usr/bin/missing")
    assert not exists("escape/passwd")


@pytest.mark.parametrize("path", ["usr/bin/t3code", "opt/a+b/c_d-1.0"])
def test_run_ebuild_accepts_staged_path(path: str) -> None:
    assert run_ebuild_fn("valid_staged_path", path).returncode == 0


@pytest.mark.parametrize("path", ["/usr/bin", "usr/../etc", "./usr", ".", "..", "usr//bin"])
def test_run_ebuild_rejects_staged_path(path: str) -> None:
    assert run_ebuild_fn("valid_staged_path", path).returncode == 1


FAKE_PORTAGEQ = """#!/bin/sh
case "$1" in
    get_repo_path) printf '%s\\n' "${FAKE_REPO_PATH-$OVERLAY_REPO}" ;;
    envvar) printf '%s\\n' "$FAKE_TMPDIR" ;;
esac
"""
# Stages usr/bin/tool into the image the way Portage's install phase would.
FAKE_EBUILD = """#!/bin/sh
[ "${FAKE_EBUILD_RC:-0}" -eq 0 ] || exit "$FAKE_EBUILD_RC"
image="$PORTAGE_TMPDIR/portage/dev-util/t3code-nightly-bin-1/image"
mkdir -p "$image/usr/bin" && touch "$image/usr/bin/tool"
"""


def run_ebuild_main(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (
        ("portageq", FAKE_PORTAGEQ),
        ("ebuild", FAKE_EBUILD),
        ("emerge", '#!/bin/sh\nexit "${FAKE_EMERGE_RC:-0}"\n'),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    repo = tmp_path / "overlay"
    if not repo.exists():
        repo.mkdir()
        overlay(repo)
    config_root = tmp_path / "root"
    (config_root / "etc/portage").mkdir(parents=True, exist_ok=True)
    (config_root / "etc/portage/make.profile").touch()
    full_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "OVERLAY_REPO": str(repo),
        "PORTAGE_CONFIGROOT": str(config_root),
        "FAKE_TMPDIR": str(tmp_path / "tmp"),
        **env,
    }
    return subprocess.run(
        ["bash", str(RUN_EBUILD), *args], capture_output=True, text=True, env=full_env, check=False
    )


def test_run_ebuild_main_passes(tmp_path: Path) -> None:
    result = run_ebuild_main(tmp_path, EBUILD, "usr/bin/tool")
    assert result.returncode == 0, result.stderr
    image = tmp_path / "tmp/portage/dev-util/t3code-nightly-bin-1/image"
    assert f"PASS: Portage phases completed; install image at {image}" in result.stdout
    conf = tmp_path / "root/etc/portage"
    assert "[turbo-overlay]" in (conf / "repos.conf/ebuild-test.conf").read_text()
    atom = "=dev-util/t3code-nightly-bin-1::turbo-overlay"
    assert (conf / "package.accept_keywords/ebuild-test").read_text() == f"{atom} ~amd64\n"


@pytest.mark.parametrize(
    ("args", "env", "code", "message"),
    [
        ([EBUILD], {"FAKE_EBUILD_RC": "3"}, 1, "Portage phases failed"),
        ([EBUILD, "usr/bin/missing"], {}, 1, "Missing staged path: usr/bin/missing"),
        ([EBUILD], {"FAKE_EMERGE_RC": "1"}, 1, "Could not install build dependencies"),
        ([EBUILD], {"FAKE_REPO_PATH": ""}, 2, "Portage did not register overlay"),
        ([EBUILD], {"FAKE_TMPDIR": ""}, 2, "Portage has no PORTAGE_TMPDIR"),
        (["dev-util/t3code-nightly-bin/t3code-nightly-bin-2.ebuild"], {}, 2, "Missing ebuild"),
    ],
)
def test_run_ebuild_main_failures(
    tmp_path: Path, args: list[str], env: dict[str, str], code: int, message: str
) -> None:
    result = run_ebuild_main(tmp_path, *args, **env)
    assert result.returncode == code
    assert message in result.stderr


def test_run_ebuild_main_requires_make_profile(tmp_path: Path) -> None:
    result = run_ebuild_main(tmp_path, EBUILD, PORTAGE_CONFIGROOT=str(tmp_path / "empty"))
    assert result.returncode == 2
    assert "Stage3 has no make.profile" in result.stderr
