"""Container runner validation and command construction tests (no Docker required)."""

import subprocess
from pathlib import Path

import pytest

from overlay_tools.cli import test_ebuild


def overlay(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/repo_name").write_text("turbo-overlay\n")
    pkg = tmp_path / "dev-util/t3code-nightly-bin"
    pkg.mkdir(parents=True)
    (pkg / "t3code-nightly-bin-1.ebuild").write_text("EAPI=8\n")
    return tmp_path


def test_validates_exact_ebuild_path(tmp_path: Path) -> None:
    root = overlay(tmp_path)
    test_ebuild.validate_ebuild(root, "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild")
    for bad in (
        "dev-util/t3code-nightly-bin",
        "../outside.ebuild",
        "/etc/passwd",
        "dev-util/t3code-nightly-bin/different-1.ebuild",
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


def test_build_and_run_mount_readonly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(test_ebuild.subprocess, "run", fake_run)
    assert (
        test_ebuild.main(
            [
                "--overlay-path",
                str(root),
                "--build",
                "--expect",
                "usr/bin/t3code",
                "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild",
            ]
        )
        == 0
    )
    assert calls[0][:4] == ["docker", "build", "-t", test_ebuild.IMAGE]
    assert calls[1][:5] == ["docker", "run", "--rm", "--platform", "linux/amd64"]
    assert calls[1][5:7] == [
        "--mount",
        f"type=bind,src={root},dst=/var/db/repos/turbo-overlay,readonly",
    ]
    assert calls[1][-2:] == [
        "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild",
        "usr/bin/t3code",
    ]


def test_refuses_invalid_expected_path_before_docker(tmp_path: Path) -> None:
    root = overlay(tmp_path)
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main(
            [
                "--overlay-path",
                str(root),
                "--expect",
                "../../etc/passwd",
                "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild",
            ]
        )
    assert exc.value.code == 2


def test_returns_container_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = overlay(tmp_path)
    monkeypatch.setattr(
        test_ebuild.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 17)
    )
    assert (
        test_ebuild.main(
            [
                "--overlay-path",
                str(root),
                "dev-util/t3code-nightly-bin/t3code-nightly-bin-1.ebuild",
            ]
        )
        == 17
    )
