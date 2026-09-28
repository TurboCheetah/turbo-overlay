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


def fake_docker(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode)

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
def test_refuses_overlay_path_breaking_mount_spec(tmp_path: Path, dirname: str) -> None:
    (tmp_path / dirname).mkdir()
    root = overlay(tmp_path / dirname)
    with pytest.raises(ValueError, match="comma or double quote"):
        test_ebuild.validate_overlay(root)


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
    assert docker_run[5:9] == [
        "--mount",
        f"type=bind,src={root},dst=/var/db/repos/turbo-overlay,readonly",
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
    calls = fake_docker(monkeypatch, returncode=1)
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main(["--overlay-path", str(root), "--build", EBUILD])
    assert exc.value.code == 2
    assert len(calls) == 1


def test_missing_docker_exits_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    calls = fake_docker(monkeypatch)

    def missing(*args: object) -> Path:
        raise ExternalToolMissingError("docker")

    monkeypatch.setattr(test_ebuild, "require_tool", missing)
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main(["--overlay-path", str(root), "--build", EBUILD])
    assert exc.value.code == 2
    assert calls == []


def test_returns_container_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    fake_docker(monkeypatch, returncode=17)
    assert test_ebuild.main(["--overlay-path", str(root), EBUILD]) == 17


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
