"""Read-only QA behavior through the public CLI and executable launcher."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

import pytest

from overlay_tools.cli import qa_ebuild

HEADER = (
    "# Copyright 1999-2026 Gentoo Authors\n"
    "# Distributed under the terms of the GNU General Public License v2\n"
)


def make_overlay(root: Path, *, thin: bool = False, atom: str = "dev-util/adapter") -> Path:
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "profiles/repo_name").write_text("test-overlay\n")
    (root / "metadata").mkdir(exist_ok=True)
    (root / "metadata/layout.conf").write_text(
        f"masters = gentoo\nthin-manifests = {str(thin).lower()}\n"
    )
    package = root / atom
    package.mkdir(parents=True)
    ebuild = package / f"{package.name}-1.ebuild"
    ebuild.write_text(HEADER + 'EAPI=8\nSRC_URI=""\nsrc_install() { :; }\n')
    (package / "metadata.xml").write_text(
        "<pkgmetadata><maintainer type='person'><email>a@example.invalid</email>"
        "</maintainer></pkgmetadata>\n"
    )
    (package / "files").mkdir()
    (package / "files/adapter.patch").write_bytes(b"local patch\n")
    cache = root / "metadata/md5-cache" / atom
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.with_name(cache.name + "-1").write_text(
        "EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n"
    )
    write_manifest(package, thin=thin)
    return root


def write_manifest(package: Path, *, thin: bool = False) -> None:
    lines = []
    if not thin:
        for kind, path in [
            ("EBUILD", next(package.glob("*.ebuild"))),
            ("MISC", package / "metadata.xml"),
            ("AUX", package / "files/adapter.patch"),
        ]:
            content = path.read_bytes()
            name = path.relative_to(package / "files") if kind == "AUX" else path.name
            lines.append(
                f"{kind} {name} {len(content)} SHA512 {hashlib.sha512(content).hexdigest()} "
                f"BLAKE2B {hashlib.blake2b(content).hexdigest()}\n"
            )
    (package / "Manifest").write_text("".join(lines))


def run(root: Path, capsys, *args: str):
    status = qa_ebuild.main(["--overlay-path", str(root), "--json", *args])
    captured = capsys.readouterr()
    return status, json.loads(captured.out)


def snapshot(root: Path):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def add_version(root: Path, atom: str, version: int) -> Path:
    package = root / atom
    ebuild = package / f"{package.name}-{version}.ebuild"
    ebuild.write_bytes((package / f"{package.name}-1.ebuild").read_bytes())
    (root / "metadata/md5-cache" / atom).with_name(ebuild.stem).write_text(
        "EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n"
    )
    return ebuild


@pytest.mark.parametrize("failure", ["missing", "oserror", "timeout"])
@pytest.mark.parametrize(
    "outcomes, expected",
    [
        ((False, False), "unchecked"),
        ((True, False), "partial"),
        ((False, True), "partial"),
        ((True, False, True), "partial"),
    ],
    ids=["none-completed", "later-failure", "first-failure", "third-recovers"],
)
@pytest.mark.parametrize("other_package", [False, True], ids=["one-package", "mixed-packages"])
def test_public_helper_bash_failures_never_claim_complete_coverage(
    tmp_path, capsys, monkeypatch, failure, outcomes, expected, other_package
):
    root = make_overlay(tmp_path / "repo", thin=True)
    ebuilds = [root / "dev-util/adapter/adapter-1.ebuild"]
    ebuilds.extend(add_version(root, "dev-util/adapter", v) for v in range(2, len(outcomes) + 1))
    if other_package:
        make_overlay(root, thin=True, atom="dev-util/zother")
    before = snapshot(root)
    which = shutil.which
    launch = subprocess.run
    attempts = []

    def find_tool(name, *args, **kwargs):
        if name == "bash":
            index = len(attempts)
            attempts.append(index)
            if failure == "missing" and index < len(outcomes) and not outcomes[index]:
                return None
        return which(name, *args, **kwargs)

    def launch_tool(argv, *args, **kwargs):
        if argv[-1] == "-n":
            index = len(attempts) - 1
            if index < len(outcomes) and not outcomes[index]:
                if failure == "oserror":
                    raise OSError("Bash launch denied")
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(argv, 30)
        return launch(argv, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", find_tool)
    monkeypatch.setattr(subprocess, "run", launch_tool)
    status, report = run(root, capsys, "--all")
    assert status == 2
    assert report["status"] == "INCONCLUSIVE"
    assert report["examined_count"] == report["selected_count"] == (2 if other_package else 1)
    assert len(attempts) == len(outcomes) + int(other_package)
    assert report["coverage"]["bash_syntax"] == ("partial" if other_package else expected)
    assert report["package_coverage"]["dev-util/adapter"]["bash_syntax"] == expected
    if other_package:
        assert report["package_coverage"]["dev-util/zother"]["bash_syntax"] == "checked"
    assert report["coverage"]["ebuild_advisory"] == "literal-header-and-eapi-only"
    assert all(
        coverage["ebuild_advisory"] == "literal-header-and-eapi-only"
        for coverage in report["package_coverage"].values()
    )
    findings = report["findings"]
    assert [finding["path"] for finding in findings] == [
        str(ebuild) for ebuild, completed in zip(ebuilds, outcomes, strict=True) if not completed
    ]
    assert all(finding["check"] == "bash_syntax" for finding in findings)
    assert all("unchecked" in finding["message"] for finding in findings)
    assert snapshot(root) == before


@pytest.mark.parametrize("other_package", [False, True], ids=["one-package", "unreached-package"])
@pytest.mark.parametrize("completed_bash", [False, True], ids=["bash-unchecked", "bash-completed"])
def test_later_ebuild_read_failure_preserves_actual_bash_and_advisory_coverage(
    tmp_path, capsys, monkeypatch, completed_bash, other_package
):
    root = make_overlay(tmp_path / "repo", thin=True)
    if other_package:
        make_overlay(root, thin=True, atom="dev-util/zother")
    ebuild = add_version(root, "dev-util/adapter", 2)
    ebuild.write_bytes(b"EAPI=8\n\xff\n")
    (root / "metadata/md5-cache/dev-util/adapter-2").write_text(
        "EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n"
    )
    if not completed_bash:
        path = tmp_path / "no-bash"
        path.mkdir()
        monkeypatch.setenv("PATH", str(path))
    before = snapshot(root)
    status, report = run(root, capsys, "--all")
    assert status == 2
    assert report["examined_count"] == 0
    assert report["selected_count"] == (2 if other_package else 1)
    if other_package:
        assert set(report["package_coverage"]["dev-util/zother"].values()) == {"not-run"}
    assert report["findings"][-1]["path"] == str(ebuild)
    for coverage in [report["coverage"], report["package_coverage"]["dev-util/adapter"]]:
        assert coverage["bash_syntax"] == ("partial" if completed_bash else "unchecked")
        assert coverage["ebuild_advisory"] == "partial"
    assert snapshot(root) == before


def test_no_source_adapter_passes_without_mutation(tmp_path, capsys):
    root = make_overlay(tmp_path)
    before = snapshot(root)
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 0
    assert report["status"] == "PASS"
    assert report["examined_count"] == 1
    assert report["packages"] == ["dev-util/adapter"]
    assert report["coverage"]["remote_dist"] == "unchecked"
    assert report["coverage"]["bash_syntax"] == "checked"
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "damage",
    ["aux", "missing-misc", "orphan-ebuild", "cache-missing", "cache-stale", "cache-orphan", "xml"],
)
def test_reports_local_damage(tmp_path, capsys, damage):
    root = make_overlay(tmp_path)
    package = root / "dev-util/adapter"
    cache = root / "metadata/md5-cache/dev-util/adapter-1"
    if damage == "aux":
        (package / "files/adapter.patch").write_text("corrupt\n")
    elif damage == "missing-misc":
        manifest = package / "Manifest"
        manifest.write_text(
            "\n".join(
                line for line in manifest.read_text().splitlines() if not line.startswith("MISC")
            )
        )
    elif damage == "orphan-ebuild":
        (package / "adapter-2.ebuild").write_text(HEADER + "EAPI=8\n")
    elif damage == "cache-missing":
        cache.unlink()
    elif damage == "cache-stale":
        cache.write_text("EAPI=8\n_md5_=00000000000000000000000000000000\n")
    elif damage == "cache-orphan":
        cache.with_name("adapter-0").write_text("EAPI=8\n")
    else:
        (package / "metadata.xml").write_text("<broken>")
    before = snapshot(root)
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 1
    assert report["findings"]
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "target",
    [
        "dev-util/adapter",
        "dev-util/adapter/adapter-1.ebuild",
        "dev-util/adapter/metadata.xml",
        "dev-util/adapter/Manifest",
        "dev-util/adapter/files",
        "dev-util/adapter/files/adapter.patch",
        "metadata/md5-cache/dev-util/adapter-1",
    ],
)
def test_explicit_targets_map_to_one_package(tmp_path, capsys, target):
    root = make_overlay(tmp_path)
    status, report = run(root, capsys, target, "dev-util/adapter")
    assert status == 0
    assert report["packages"] == ["dev-util/adapter"]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["dev-util/missing"],
        ["../outside"],
        ["dev-util/adapter", "--all"],
        ["--all", "--changed-since", "HEAD"],
    ],
)
def test_invalid_or_empty_selection_is_inconclusive(tmp_path, capsys, args):
    root = make_overlay(tmp_path)
    status, report = run(root, capsys, *args)
    assert status == 2
    assert report["status"] == "INCONCLUSIVE"
    assert report["examined_count"] == 0


def test_all_selects_packages_and_thin_manifest_does_not_require_local_hashes(tmp_path, capsys):
    root = make_overlay(tmp_path, thin=True)
    status, report = run(root, capsys, "--all")
    assert status == 0
    assert report["coverage"]["manifest_local"] == "thin-records-only"
    assert any(item["check"] == "manifest_local_coverage" for item in report["skipped"])


@pytest.mark.parametrize(
    "location",
    [
        "dev-util/adapter/files/adapter.patch",
        "dev-util/adapter/metadata.xml",
        "metadata/layout.conf",
        "metadata/md5-cache/dev-util/adapter-1",
    ],
)
def test_symlink_escape_is_rejected_before_checks(tmp_path, capsys, location):
    root = make_overlay(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.write_text("secret\n")
    path = root / location
    path.unlink()
    path.symlink_to(outside)
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 2
    assert report["coverage"]["bash_syntax"] == "not-run"
    assert outside.read_text() == "secret\n"


@pytest.mark.parametrize("name", ["../../outside", "/etc/passwd", "../metadata.xml"])
def test_hostile_manifest_paths_fail_closed(tmp_path, capsys, name):
    root = make_overlay(tmp_path)
    (root / "dev-util/adapter/Manifest").write_text(f"AUX {name} 1 SHA512 00\n")
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 2
    assert report["examined_count"] == 0
    assert report["coverage"]["bash_syntax"] == "not-run"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def test_changed_since_includes_commits_staged_unstaged_and_reports_untracked(tmp_path, capsys):
    root = make_overlay(tmp_path, thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    git(root, "add", "dev-util/adapter/metadata.xml")
    git(
        root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "change"
    )
    (root / "dev-util/other/files/adapter.patch").write_text("staged\n")
    git(root, "add", "dev-util/other/files/adapter.patch")
    (root / "dev-util/other/files/adapter.patch").write_text("local patch\n")
    (root / "dev-util/adapter/files/untracked.patch").write_text("untracked\n")
    before = snapshot(root)
    status, report = run(root, capsys, "--changed-since", base)
    assert status == 0
    assert report["packages"] == ["dev-util/adapter", "dev-util/other"]
    assert any(item["check"] == "untracked" for item in report["skipped"])
    assert snapshot(root) == before


@pytest.mark.parametrize("ref", ["missing-ref", "--output=/tmp/not-allowed"])
def test_invalid_git_ref_does_not_select_packages(tmp_path, capsys, ref):
    root = make_overlay(tmp_path)
    status, report = run(root, capsys, "--changed-since", ref)
    assert status == 2
    assert report["examined_count"] == 0


@pytest.mark.parametrize("exit_status", [0, 1, 2, 127])
def test_pkgcheck_opt_in_scopes_argv_and_propagates_failures(
    tmp_path, capsys, monkeypatch, exit_status
):
    root = make_overlay(tmp_path / "repo")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "argv.json"
    script = tools / "pkgcheck"
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.exit({exit_status})\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    status, report = run(root, capsys, "dev-util/adapter", "--pkgcheck")
    assert status == (0 if exit_status == 0 else 1 if exit_status == 1 else 2)
    argv = json.loads(log.read_text())
    assert argv[0] == "scan"
    assert argv[argv.index("--repo") + 1] == str(root)
    assert argv[-1] == "dev-util/adapter"
    assert argv[argv.index("--cache") + 1] == "no"
    assert argv[argv.index("--config") + 1] == "no"
    assert "--net" not in argv
    assert report["coverage"]["pkgcheck"] != "not-run"


def test_missing_pkgcheck_is_inconclusive(tmp_path, capsys, monkeypatch):
    root = make_overlay(tmp_path / "repo")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "bash").symlink_to("/bin/bash")
    monkeypatch.setenv("PATH", str(tools))
    status, report = run(root, capsys, "dev-util/adapter", "--pkgcheck")
    assert status == 2
    assert report["coverage"]["pkgcheck"] == "unavailable"


def test_default_never_launches_pkgcheck(tmp_path, capsys, monkeypatch):
    root = make_overlay(tmp_path / "repo")
    tools = tmp_path / "tools"
    tools.mkdir()
    sentinel = tmp_path / "pkgcheck-launched"
    executable = tools / "pkgcheck"
    executable.write_text(f"#!/bin/bash\ntouch {sentinel}\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    assert run(root, capsys, "dev-util/adapter")[0] == 0
    assert not sentinel.exists()


def public_launcher(tmp_path: Path, name: str) -> Path:
    """Run the real launcher with this test run's already provisioned interpreter."""
    tools = Path(__file__).resolve().parents[1]
    copy = tmp_path / "public-tools"
    (copy / "bin").mkdir(parents=True)
    launcher = copy / "bin" / name
    launcher.write_bytes((tools / "bin" / name).read_bytes())
    launcher.chmod(0o755)
    (copy / "src").symlink_to(tools / "src", target_is_directory=True)
    (copy / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    return launcher


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("configuration", ["default", "local", "include", "worktree"])
@pytest.mark.parametrize("change", ["staged", "unstaged", "staged-and-restored"])
def test_public_changed_selection_preserves_index_and_checkout(
    tmp_path, name, configuration, change
):
    root = make_overlay(tmp_path / "repo", thin=True)
    patch = root / "dev-util/adapter/files/adapter.patch"
    patch.write_bytes(b"alpha\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    if configuration == "include":
        config = root / ".git/diff.config"
        git(root, "config", "--file", str(config), "diff.autoRefreshIndex", "true")
        git(root, "config", "include.path", str(config))
    elif configuration == "worktree":
        git(root, "config", "extensions.worktreeConfig", "true")
        git(root, "config", "--worktree", "diff.autoRefreshIndex", "true")
    elif configuration == "local":
        git(root, "config", "diff.autoRefreshIndex", "true")
    launcher = public_launcher(tmp_path, name)
    patch.write_bytes(b"bravo\n")
    if change != "unstaged":
        git(root, "add", "dev-util/adapter/files/adapter.patch")
    if change == "staged-and-restored":
        # Base/worktree bytes match, but both index/base and worktree/index differ.
        # The first diff can otherwise smudge the index's cached stat size to zero.
        patch.write_bytes(b"alpha\n")
    # No status/diff before QA: refreshing first can hide an index write.
    index = root / ".git/index"
    before_index = index.read_bytes(), index.stat().st_mtime_ns
    before = snapshot(root)
    before_mtimes = {
        str(path.relative_to(root)): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file()
    }
    for _ in range(2):
        result = subprocess.run(
            [str(launcher), "--overlay-path", str(root), "--changed-since", "HEAD"]
            + (["--json"] if name == "qa-ebuild" else []),
            capture_output=True,
            text=True,
            check=False,
        )
        # Check each invocation against the original snapshot, not a refreshed baseline.
        assert (index.read_bytes(), index.stat().st_mtime_ns) == before_index
        assert snapshot(root) == before
        assert {
            str(path.relative_to(root)): path.stat().st_mtime_ns
            for path in root.rglob("*")
            if path.is_file()
        } == before_mtimes
        assert result.returncode == 0, result.stdout + result.stderr
        if name == "qa-ebuild":
            report = json.loads(result.stdout)
            assert report["status"] == "PASS"
            assert report["packages"] == report["selected_packages"] == ["dev-util/adapter"]
            assert report["examined_count"] == report["selected_count"] == 1
            assert report["coverage"]["selection"] == "git-base-plus-index-and-worktree"
        else:
            assert "PASS: examined 1 package(s)" in result.stdout
            assert "dev-util/adapter" in result.stdout
            assert "not applicable" not in result.stdout


def configure_index_refresh(root: Path, configuration: str) -> None:
    if configuration == "include":
        config = root / ".git/diff.config"
        git(root, "config", "--file", str(config), "diff.autoRefreshIndex", "true")
        git(root, "config", "include.path", str(config))
    elif configuration == "worktree":
        git(root, "config", "extensions.worktreeConfig", "true")
        git(root, "config", "--worktree", "diff.autoRefreshIndex", "true")
    elif configuration == "local":
        git(root, "config", "diff.autoRefreshIndex", "true")


def checkout_state(root: Path):
    """Snapshot modes, link targets, file bytes and mtimes, including every Git index."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            os.readlink(path)
            if path.is_symlink()
            else path.read_bytes()
            if path.is_file()
            else None,
        )
        for path in root.rglob("*")
    }


def repeat_changed_launcher(tmp_path: Path, root: Path, name: str, selected: list[str], ref="HEAD"):
    launcher = public_launcher(tmp_path, name)
    before = checkout_state(root)
    for _ in range(2):
        result = subprocess.run(
            [str(launcher), "--overlay-path", str(root), "--changed-since", ref]
            + (["--json"] if name == "qa-ebuild" else []),
            capture_output=True,
            text=True,
            check=False,
        )
        assert checkout_state(root) == before
        if name == "qa-ebuild":
            report = json.loads(result.stdout)
            assert report["selected_packages"] == report["packages"] == selected
            assert report["selected_count"] == report["examined_count"] == len(selected)
            assert report["coverage"]["selection"] == "git-base-plus-index-and-worktree"
            assert report["status"] == ("PASS" if selected else "INCONCLUSIVE")
            assert result.returncode == (0 if selected else 2), result.stdout + result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            if selected:
                assert f"PASS: examined {len(selected)} package(s)" in result.stdout
                assert "not applicable" not in result.stdout
                for atom in selected:
                    assert atom in result.stdout
            else:
                assert "not applicable: no package targets changed" in result.stdout
                assert "PASS: examined" not in result.stdout


def rewrite_stat_only(path: Path) -> None:
    original = path.stat()
    path.write_bytes(path.read_bytes())
    # A controlled, non-racy mtime mismatch. No sleeps or Git refresh commands.
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns - 10_000_000_000))


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("configuration", ["default", "local", "include", "worktree"])
@pytest.mark.parametrize(
    "change",
    [
        "shared-stat",
        "package-stat",
        "untracked-with-shared-stat",
        "shared-content",
        "staged-restored-with-shared-stat",
        "package-content-with-shared-stat",
    ],
)
def test_public_content_selection_ignores_stat_only_changes(tmp_path, name, configuration, change):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    patch = root / "dev-util/adapter/files/adapter.patch"
    patch.write_bytes(b"alpha\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    configure_index_refresh(root, configuration)
    selected = []
    if change == "package-stat":
        rewrite_stat_only(patch)
    else:
        for relative in ["metadata/layout.conf", "profiles/repo_name"]:
            rewrite_stat_only(root / relative)
    if change == "untracked-with-shared-stat":
        # This helper also rewrites the existing shared files with identical bytes.
        make_overlay(root, thin=True, atom="dev-util/new")
        for relative in ["metadata/layout.conf", "profiles/repo_name"]:
            rewrite_stat_only(root / relative)
    elif change == "shared-content":
        (root / "profiles/repo_name").write_text("real-change\n")
        selected = ["dev-util/adapter", "dev-util/other"]
    elif change in {"staged-restored-with-shared-stat", "package-content-with-shared-stat"}:
        patch.write_bytes(b"bravo\n")
        if change == "staged-restored-with-shared-stat":
            git(root, "add", "dev-util/adapter/files/adapter.patch")
            patch.write_bytes(b"alpha\n")
        selected = ["dev-util/adapter"]
    repeat_changed_launcher(tmp_path, root, name, selected)


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("configuration", ["default", "local", "include", "worktree"])
def test_public_content_selection_keeps_exact_three_way_union(tmp_path, name, configuration):
    root = make_overlay(tmp_path / "repo", thin=True)
    for package in ["staged", "unstaged", "untouched"]:
        make_overlay(root, thin=True, atom=f"dev-util/{package}")
    patch = root / "dev-util/staged/files/adapter.patch"
    patch.write_bytes(b"alpha\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "dev-util/adapter/files/adapter.patch").write_text("committed\n")
    git(root, "add", "dev-util/adapter")
    git(
        root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "change"
    )
    configure_index_refresh(root, configuration)
    patch.write_bytes(b"bravo\n")
    git(root, "add", "dev-util/staged/files/adapter.patch")
    patch.write_bytes(b"alpha\n")
    (root / "dev-util/unstaged/files/adapter.patch").write_text("unstaged\n")
    for relative in ["metadata/layout.conf", "profiles/repo_name"]:
        rewrite_stat_only(root / relative)
    repeat_changed_launcher(
        tmp_path, root, name, ["dev-util/adapter", "dev-util/staged", "dev-util/unstaged"], base
    )


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize(
    "change",
    [
        "mode",
        "delete",
        "symlink-target",
        "file-to-symlink",
        "symlink-to-file",
        "rename",
        "binary",
        "empty-add",
        "empty-delete",
        "unusual-name",
        "crlf",
        "ident",
    ],
)
def test_public_content_selection_preserves_git_file_semantics(tmp_path, name, change):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    patch = root / "dev-util/adapter/files/adapter.patch"
    if change in {"empty-add", "empty-delete"}:
        patch.write_bytes(b"")
    elif change == "binary":
        patch.write_bytes(b"alpha\x00\n")
    elif change == "unusual-name":
        patch = patch.with_name("tab\tline\nnonutf8-" + os.fsdecode(b"\xff"))
        patch.write_bytes(b"alpha\n")
    elif change in {"symlink-target", "symlink-to-file"}:
        patch.unlink()
        patch.symlink_to("../metadata.xml")
    elif change in {"crlf", "ident"}:
        attribute = "text eol=lf" if change == "crlf" else "ident"
        (root / ".gitattributes").write_text(f"*.patch {attribute}\n")
        patch.write_bytes(b"alpha\n" if change == "crlf" else b"$Id$\n")
    git(root, "init", "-q")
    git(root, "config", "core.filemode", "true")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    selected = ["dev-util/adapter"]
    if change == "mode":
        patch.chmod(0o755)
    elif change in {"delete", "empty-delete"}:
        patch.unlink()
    elif change == "symlink-target":
        patch.unlink()
        patch.symlink_to("../adapter-1.ebuild")
    elif change == "file-to-symlink":
        patch.unlink()
        patch.symlink_to("../metadata.xml")
    elif change == "symlink-to-file":
        patch.unlink()
        patch.write_bytes(b"regular\n")
    elif change == "rename":
        git(root, "mv", str(patch), "dev-util/other/files/renamed.patch")
        selected.append("dev-util/other")
    elif change == "binary":
        patch.write_bytes(b"bravo\x00\n")
    elif change == "empty-add":
        patch.with_name("added.patch").write_bytes(b"")
        git(root, "add", "dev-util/adapter/files/added.patch")
    elif change == "unusual-name":
        patch.write_bytes(b"bravo\n")
    else:
        # Git normalization, not raw-byte equality, defines tracked changes.
        patch.write_bytes(b"alpha\r\n" if change == "crlf" else b"$Id: expanded value $\n")
        selected = []
    for relative in ["metadata/layout.conf", "profiles/repo_name"]:
        rewrite_stat_only(root / relative)
    repeat_changed_launcher(tmp_path, root, name, selected)


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("missing_at", [0, 1], ids=["missing-first", "disappears-later"])
def test_real_launchers_report_missing_bash_across_versions(tmp_path, name, missing_at):
    root = make_overlay(tmp_path / "repo", thin=True)
    second = add_version(root, "dev-util/adapter", 2)
    launcher = public_launcher(tmp_path, name)
    tools = tmp_path / "path"
    tools.mkdir()
    (tools / "python3").symlink_to(sys.executable)
    if missing_at:
        bash = tools / "bash"
        # QA's first Bash check removes the only Bash visible to later checks.
        # Syntax input is parsed, not run.
        bash.write_text(
            '#!/bin/bash\nif [[ "$1" == "--noprofile" ]]; then\n'
            '  /bin/rm -- "$0"\nfi\nexec /bin/bash "$@"\n'
        )
        bash.chmod(0o755)
    before = snapshot(root)
    args = (
        ["--all", "--json"] if name == "qa-ebuild" else ["--informational", "metadata/layout.conf"]
    )
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *args],
        env={**os.environ, "PATH": str(tools)},
        capture_output=True,
        text=True,
        check=False,
    )
    expected = "partial" if missing_at else "unchecked"
    assert result.returncode == (2 if name == "qa-ebuild" else 0), result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["status"] == "INCONCLUSIVE"
        assert report["examined_count"] == report["selected_count"] == 1
        assert report["coverage"]["bash_syntax"] == expected
        assert report["package_coverage"]["dev-util/adapter"]["bash_syntax"] == expected
        assert report["coverage"]["ebuild_advisory"] == "literal-header-and-eapi-only"
        assert [finding["path"] for finding in report["findings"]] == (
            [str(second)]
            if missing_at
            else [str(second.with_name("adapter-1.ebuild")), str(second)]
        )
        assert all("syntax unchecked" in finding["message"] for finding in report["findings"])
    else:
        assert "INCONCLUSIVE: examined 1 package(s)" in result.stdout
        assert f"'bash_syntax': '{expected}'" in result.stdout
        assert "'ebuild_advisory': 'literal-header-and-eapi-only'" in result.stdout
        assert "authoritative QA exit: 2; informational wrapper exit: 0" in result.stdout
        assert f"bash_syntax: {second}: bash is unavailable; syntax unchecked" in result.stdout
        assert "PASS:" not in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize("encoding", ["nonexistent", "UTF-32", "UTF-16"])
@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
def test_public_launchers_report_invalid_xml_encoding_as_finding(tmp_path, encoding, name):
    root = make_overlay(tmp_path / "repo", thin=True)
    xml = root / "dev-util/adapter/metadata.xml"
    xml.write_bytes(f'<?xml version="1.0" encoding="{encoding}"?><pkgmetadata/>'.encode("ascii"))
    launcher = public_launcher(tmp_path, name)
    before = snapshot(root)
    args = (
        ["--all", "--json"]
        if name == "qa-ebuild"
        else ["--informational", "dev-util/adapter/metadata.xml"]
    )
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if name == "qa-ebuild" else 0), result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["status"] == "FAIL"
        assert report["examined_count"] == 1
        assert report["inconclusive"] is False
        assert report["coverage"]["metadata_xml"] == "checked"
        assert len(report["findings"]) == 1
        finding = report["findings"][0]
        assert finding["check"] == "metadata_xml"
        assert finding["path"] == str(xml)
        assert finding["severity"] == "error"
        assert finding["message"].startswith("Invalid XML:")
    else:
        assert "FAIL: examined 1 package(s)" in result.stdout
        assert f"metadata_xml: {xml}: Invalid XML:" in result.stdout
        assert "authoritative QA exit: 1; informational wrapper exit: 0" in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize("selection", ["all", "ordered", "reversed"])
def test_interrupted_package_scan_reports_exact_path_and_partial_coverage(tmp_path, selection):
    root = make_overlay(tmp_path / "repo")
    make_overlay(root, atom="dev-util/zother")
    damaged = root / "dev-util/adapter/files/adapter.patch"
    damaged.write_bytes(b"known damage\n")
    cache = root / "metadata/md5-cache/dev-util/zother-1"
    cache.write_bytes(b"EAPI=8\n\xff\n")
    launcher = public_launcher(tmp_path, "qa-ebuild")
    targets = {
        "all": ["--all"],
        "ordered": ["dev-util/adapter", "dev-util/zother"],
        "reversed": ["dev-util/zother", "dev-util/adapter"],
    }[selection]
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--json", *targets],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "INCONCLUSIVE"
    assert report["inconclusive"] is True
    assert report["examined_count"] == 1
    assert report["packages"] == ["dev-util/adapter"]
    assert report["selected_count"] == 2
    assert report["selected_packages"] == ["dev-util/adapter", "dev-util/zother"]
    assert report["coverage"] == {
        "metadata_xml": "checked",
        "manifest_local": "full-local",
        "cache": "partial",
        "ebuild_advisory": "partial",
        "bash_syntax": "partial",
        "pkgcheck": "not-run",
        "remote_dist": "unchecked",
        "bash_metadata": "unchecked",
    }
    assert report["package_coverage"] == {
        "dev-util/adapter": {
            "metadata_xml": "checked",
            "manifest_local": "full-local",
            "cache": "checked-local-ebuild-fingerprints",
            "ebuild_advisory": "literal-header-and-eapi-only",
            "bash_syntax": "checked",
        },
        "dev-util/zother": {
            "metadata_xml": "checked",
            "manifest_local": "full-local",
            "cache": "unchecked",
            "ebuild_advisory": "not-run",
            "bash_syntax": "not-run",
        },
    }
    failures = [item for item in report["findings"] if item["check"] == "input_environment"]
    assert len(failures) == 1
    assert failures[0]["path"] == str(cache)
    assert "dev-util/zother" in failures[0]["message"]
    assert any(
        item["path"] == str(damaged) and "SHA512 mismatch" in item["message"]
        for item in report["findings"]
    )
    assert any(
        item["check"] == "package_scan"
        and item["path"] == str(root / "dev-util/zother")
        and "incomplete" in item["reason"]
        for item in report["skipped"]
    )
    assert snapshot(root) == before


@pytest.mark.parametrize("damage", ["cache", "ebuild"])
def test_interrupted_second_version_never_claims_completed_package_coverage(tmp_path, damage):
    root = make_overlay(tmp_path / "repo", thin=True)
    package = root / "dev-util/adapter"
    ebuild = package / "adapter-2.ebuild"
    ebuild.write_bytes(b"EAPI=8\n\xff\n" if damage == "ebuild" else b"EAPI=8\n")
    cache = root / "metadata/md5-cache/dev-util/adapter-2"
    cache.write_bytes(
        b"EAPI=8\n\xff\n"
        if damage == "cache"
        else ("EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n").encode()
    )
    launcher = public_launcher(tmp_path, "qa-ebuild")
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--all", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["examined_count"] == 0
    assert report["selected_count"] == 1
    assert report["findings"][-1]["path"] == str(cache if damage == "cache" else ebuild)
    coverage = report["package_coverage"]["dev-util/adapter"]
    assert coverage["cache"] == (
        "partial" if damage == "cache" else "checked-local-ebuild-fingerprints"
    )
    assert coverage["ebuild_advisory"] == ("not-run" if damage == "cache" else "partial")
    assert coverage["bash_syntax"] == ("not-run" if damage == "cache" else "partial")
    assert snapshot(root) == before


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
def test_interruption_keeps_later_packages_unchecked_in_json_and_human_output(tmp_path, name):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/middle")
    make_overlay(root, thin=True, atom="dev-util/zlast")
    cache = root / "metadata/md5-cache/dev-util/middle-1"
    cache.write_bytes(b"\xff\n")
    launcher = public_launcher(tmp_path, name)
    args = (
        ["--all", "--json"] if name == "qa-ebuild" else ["--informational", "metadata/layout.conf"]
    )
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (2 if name == "qa-ebuild" else 0)
    assert "Traceback" not in result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["examined_count"] == 1
        assert report["selected_count"] == 3
        assert report["selected_packages"] == [
            "dev-util/adapter",
            "dev-util/middle",
            "dev-util/zlast",
        ]
        assert set(report["package_coverage"]["dev-util/zlast"].values()) == {"not-run"}
        assert {report["coverage"][check] for check in ["cache", "bash_syntax"]} == {"partial"}
        assert report["findings"][-1]["path"] == str(cache)
    else:
        assert "INCONCLUSIVE: examined 1 package(s)" in result.stdout
        assert "Selected 3 package(s)" in result.stdout
        assert "package coverage: dev-util/zlast:" in result.stdout
        assert "'cache': 'partial'" in result.stdout
        assert (
            f"input_environment: {cache}: Package dev-util/middle scan incomplete" in result.stdout
        )
        assert "authoritative QA exit: 2; informational wrapper exit: 0" in result.stdout
        assert "PASS:" not in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize("location", ["files/cycle", "metadata.xml", "layout", "root"])
@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
def test_public_launchers_report_symlink_loops_as_inconclusive(tmp_path, location, name):
    root = make_overlay(tmp_path / "repo", thin=True)
    if location == "root":
        root = tmp_path / "cycle"
        root.symlink_to(root.name)
    elif location == "layout":
        path = root / "metadata/layout.conf"
        path.unlink()
        path.symlink_to(path.name)
    else:
        path = root / "dev-util/adapter" / location
        if path.exists():
            path.unlink()
        path.symlink_to(path.name)
    launcher = public_launcher(tmp_path, name)
    args = (
        ["--all", "--json"]
        if name == "qa-ebuild"
        else ["--informational", "dev-util/adapter/metadata.xml"]
    )
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (2 if name == "qa-ebuild" else 0), result.stderr
    assert "Traceback" not in result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["status"] == "INCONCLUSIVE"
        assert report["examined_count"] == 0
        assert report["coverage"]["bash_syntax"] == "not-run"
        assert any(item["check"] == "input_environment" for item in report["findings"])
    else:
        assert "INCONCLUSIVE: examined 0 package(s)" in result.stdout
        assert "authoritative QA exit: 2; informational wrapper exit: 0" in result.stdout


@pytest.mark.parametrize("thin", [False, True])
def test_public_launcher_missing_manifest_respects_layout_and_reports_coverage(tmp_path, thin):
    root = make_overlay(tmp_path / "repo", thin=thin)
    (root / "dev-util/adapter/Manifest").unlink()
    launcher = public_launcher(tmp_path, "qa-ebuild")
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--all", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (0 if thin else 1), report
    assert report["examined_count"] == 1
    assert report["coverage"]["manifest_local"] == ("unchecked" if thin else "missing-manifest")
    assert report["coverage"]["remote_dist"] == "unchecked"
    assert report["coverage"]["bash_metadata"] == "unchecked"
    manifest_findings = [item for item in report["findings"] if item["check"] == "manifest_local"]
    assert bool(manifest_findings) is (not thin)
    if thin:
        assert any(
            item["check"] == "manifest_local_coverage" and "Manifest" in item["reason"]
            for item in report["skipped"]
        )
    assert snapshot(root) == before


def test_public_launcher_thin_manifest_still_validates_present_local_records(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    (root / "dev-util/adapter/Manifest").write_text("AUX adapter.patch 12 SHA512 00\n")
    launcher = public_launcher(tmp_path, "qa-ebuild")
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--all", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert any(
        "SHA512 mismatch" in item["message"] for item in json.loads(result.stdout)["findings"]
    )


@pytest.mark.parametrize("ref", ["", " ", "\t"])
@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("targets", [[], ["dev-util/adapter"]])
def test_public_launchers_reject_blank_changed_since_without_fallback(tmp_path, ref, name, targets):
    root = make_overlay(tmp_path / "repo", thin=True)
    launcher = public_launcher(tmp_path, name)
    args = ["--json"] if name == "qa-ebuild" else []
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since=" + ref, *targets, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not applicable" not in result.stdout
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["status"] == "INCONCLUSIVE"
        assert report["examined_count"] == 0
        assert report["coverage"]["bash_syntax"] == "not-run"
        assert any(
            "Invalid Git base reference" in item["message"]
            or "cannot be combined" in item["message"]
            for item in report["findings"]
        )
    else:
        assert "INCONCLUSIVE: examined 0 package(s)" in result.stdout
        assert (
            "Invalid Git base reference" in result.stdout or "cannot be combined" in result.stdout
        )


@pytest.mark.parametrize("driver", ["clean", "process"])
@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("configuration", ["local", "include", "worktree"])
def test_public_changed_selection_never_executes_git_filters(tmp_path, driver, name, configuration):
    root = make_overlay(tmp_path / "repo", thin=True)
    (root / ".gitattributes").write_text("*.patch filter=probe\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    # Configure only after committing, so setup cannot execute the filter.
    marker = tmp_path / "FILTER_EXECUTED"
    helper = tmp_path / "filter-probe"
    helper.write_text(
        f"#!/bin/bash\nprintf executed > {str(marker)!r}\n"
        + ("exec /bin/cat\n" if driver == "clean" else "exit 1\n")
    )
    helper.chmod(0o755)
    if configuration == "include":
        config = tmp_path / "filter.config"
        git(root, "config", "--file", str(config), f"filter.probe.{driver}", str(helper))
        git(root, "config", "include.path", str(config))
    elif configuration == "worktree":
        git(root, "config", "extensions.worktreeConfig", "true")
        git(root, "config", "--worktree", f"filter.probe.{driver}", str(helper))
    else:
        git(root, "config", f"filter.probe.{driver}", str(helper))
    # Same-size content change forces Git to compare bytes, not just file length.
    (root / "dev-util/adapter/files/adapter.patch").write_bytes(b"other patch\n")
    before = snapshot(root)
    launcher = public_launcher(tmp_path, name)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", "HEAD"]
        + (["--json"] if name == "qa-ebuild" else []),
        capture_output=True,
        text=True,
        check=False,
    )
    assert not marker.exists(), "Read-only Git selection executed a configured filter"
    assert result.returncode == 2, result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["status"] == "INCONCLUSIVE"
        assert report["examined_count"] == 0
        assert report["coverage"]["selection"] == "unchecked"
        assert report["coverage"]["bash_syntax"] == "not-run"
        assert any("filter" in item["message"] for item in report["findings"])
    else:
        assert "INCONCLUSIVE: examined 0 package(s)" in result.stdout
        assert "not applicable" not in result.stdout
        assert "filter" in result.stdout
    assert snapshot(root) == before


def option_prefixes(options: list[str]) -> list[str]:
    return sorted({option[:end] for option in options for end in range(3, len(option))})


def external_tool_spies(tmp_path: Path) -> tuple[dict[str, str], Path]:
    tools = tmp_path / "external-tools"
    tools.mkdir()
    log = tmp_path / "EXTERNAL_TOOL_EXECUTED"
    for name in ["pkgcheck", "egencache", "ebuild", "uv", "git"]:
        executable = tools / name
        executable.write_text(f"#!/bin/bash\nprintf '%s\\n' {name} >> {str(log)!r}\n")
        executable.chmod(0o755)
    return {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"]}, log


@pytest.mark.parametrize(
    "prefix",
    option_prefixes(
        ["--pkgcheck", "--all", "--json", "--overlay-path", "--changed-since", "--help"]
    ),
)
def test_public_launcher_rejects_abbreviated_options_without_checks(tmp_path, prefix):
    root = make_overlay(tmp_path / "repo")
    environment, log = external_tool_spies(tmp_path)
    launcher = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    value = [str(root)] if "--overlay-path".startswith(prefix) else []
    if "--changed-since".startswith(prefix):
        value = ["HEAD"]
    before = snapshot(tmp_path)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--json", "dev-util/adapter", prefix, *value],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "INCONCLUSIVE"
    assert report["examined_count"] == 0
    assert any("unrecognized arguments" in item["message"] for item in report["findings"])
    assert report["coverage"]["pkgcheck"] == "not-run"
    assert report["coverage"]["bash_syntax"] == "not-run"
    assert not log.exists()
    assert snapshot(tmp_path) == before


def assert_launcher_import_isolation(tmp_path: Path, name: str, source: str) -> None:
    """Exercise a real launcher with a competing package or Python startup hook."""
    root = make_overlay(tmp_path / "repo")
    tools = Path(__file__).resolve().parents[1]
    launcher = tools / "bin" / name
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    sentinel = tmp_path / "SHADOW_EXECUTED"
    payload = (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed')\n"
        "print('SHADOW_IMPORT')\n"
    )
    if source == "sitecustomize":
        (decoy / "sitecustomize.py").write_text(payload)
    else:
        package = decoy / "overlay_tools/cli"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / f"{name.replace('-', '_')}.py").write_text(payload)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    environment["PATH"] = "/usr/bin:/bin"
    cwd = decoy if source == "cwd" else tmp_path
    if source in {"pythonpath", "sitecustomize"}:
        environment["PYTHONPATH"] = str(decoy)
    elif source == "other-editable":
        # A plain-path .pth is also how setuptools installs this editable package.
        # Use a separate provisioned venv, never change this checkout's environment.
        copied_tools = tmp_path / "tools with 'quotes' $(touch INJECTED)"
        (copied_tools / "bin").mkdir(parents=True)
        copied_launcher = copied_tools / "bin" / name
        copied_launcher.write_bytes(launcher.read_bytes())
        copied_launcher.chmod(0o755)
        (copied_tools / "src").symlink_to(tools / "src", target_is_directory=True)
        venv.EnvBuilder(with_pip=False).create(copied_tools / ".venv")
        site_packages = next((copied_tools / ".venv/lib").glob("python*/site-packages"))
        (site_packages / "_editable_overlay_tools.pth").write_text(str(decoy) + "\n")
        # Reuse installed dependencies without invoking any installer or network tool.
        (site_packages / "dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n")
        launcher = copied_launcher
    arguments = ["--initial"] if name == "qa-ebuild-check" else ["--all", "--json"]
    before = snapshot(tmp_path)
    before_source = snapshot(tools / "src")
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "SHADOW_IMPORT" not in result.stdout + result.stderr
    assert not sentinel.exists(), "The launcher executed an untrusted Python import"
    assert result.returncode == 0, result.stdout + result.stderr
    if name == "qa-ebuild-check":
        assert "PASS: examined 1 package(s)" in result.stdout
    else:
        assert json.loads(result.stdout)["packages"] == ["dev-util/adapter"]
    assert snapshot(tmp_path) == before
    assert snapshot(tools / "src") == before_source


@pytest.mark.parametrize("source", ["cwd", "pythonpath", "sitecustomize", "other-editable"])
def test_launcher_imports_own_source_in_isolated_python(tmp_path, source):
    assert_launcher_import_isolation(tmp_path, "qa-ebuild", source)


def test_launcher_from_other_cwd_needs_no_install_or_network(tmp_path):
    root = make_overlay(tmp_path / "repo")
    launcher = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "dev-util/adapter", "--json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["examined_count"] == 1
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "source",
    [
        "touch SHOULD_NOT_EXIST\n",
        "X=$(touch SHOULD_NOT_EXIST)\n",
        "# PATCHES is merely a comment\nDESCRIPTION='PATCHES in a string'\n",
    ],
)
def test_syntax_check_does_not_execute_or_infer_patch_handling(
    tmp_path, capsys, monkeypatch, source
):
    root = make_overlay(tmp_path)
    package = root / "dev-util/adapter"
    ebuild = package / "adapter-1.ebuild"
    ebuild.write_text(HEADER + "EAPI=8\n" + source)
    (root / "metadata/md5-cache/dev-util/adapter-1").write_text(
        "EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n"
    )
    write_manifest(package)
    startup = tmp_path / "startup"
    startup.write_text("touch SHOULD_NOT_EXIST\n")
    monkeypatch.setenv("BASH_ENV", str(startup))
    before = snapshot(root)
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 0
    assert report["coverage"]["bash_syntax"] == "checked"
    assert snapshot(root) == before
    assert not (root / "SHOULD_NOT_EXIST").exists()


def test_special_file_is_rejected_without_opening_it(tmp_path, capsys):
    root = make_overlay(tmp_path)
    path = root / "dev-util/adapter/files/adapter.patch"
    path.unlink()
    os.mkfifo(path)
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 2
    assert report["examined_count"] == 0


def test_hostile_manifest_in_later_package_prevents_any_examination(tmp_path, capsys):
    root = make_overlay(tmp_path)
    make_overlay(root, atom="dev-util/zother")
    (root / "dev-util/zother/Manifest").write_text("AUX ../../escape 1 SHA512 00\n")
    status, report = run(root, capsys, "--all")
    assert status == 2
    assert report["examined_count"] == 0
    assert report["coverage"]["bash_syntax"] == "not-run"


def test_variable_length_hash_is_a_finding_not_a_crash(tmp_path, capsys):
    root = make_overlay(tmp_path)
    manifest = root / "dev-util/adapter/Manifest"
    manifest.write_text(manifest.read_text() + "AUX adapter.patch 12 SHAKE_128 00\n")
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 1
    assert any("Unsupported hash" in item["message"] for item in report["findings"])


def test_missing_environment_launcher_is_inconclusive_without_bootstrap(tmp_path):
    tools = tmp_path / "tools"
    (tools / "bin").mkdir(parents=True)
    original = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    launcher = tools / "bin/qa-ebuild"
    launcher.write_bytes(original.read_bytes())
    launcher.chmod(0o755)
    result = subprocess.run(
        [str(launcher), "--json", "--all", "--overlay-path", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "INCONCLUSIVE"
    assert set(report) == {
        "status",
        "examined_count",
        "selected_count",
        "packages",
        "selected_packages",
        "package_coverage",
        "inconclusive",
        "findings",
        "skipped",
        "coverage",
    }
    assert report["examined_count"] == report["selected_count"] == 0
    assert report["packages"] == report["selected_packages"] == []
    assert report["package_coverage"] == {}
    assert report["inconclusive"] is True
    assert report["coverage"] == {
        "remote_dist": "unchecked",
        "bash_metadata": "unchecked",
        "manifest_local": "not-run",
        "metadata_xml": "not-run",
        "cache": "not-run",
        "bash_syntax": "not-run",
        "ebuild_advisory": "not-run",
        "pkgcheck": "not-run",
    }
    prepared = public_launcher(tmp_path, "qa-ebuild")
    invalid = subprocess.run(
        [str(prepared), "--json", "--bad"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert report["skipped"] == json.loads(invalid.stdout)["skipped"]
    assert not (tools / ".venv").exists()


@pytest.mark.parametrize(
    "change", ["none", "untracked", "cache-delete", "eclass", "package-delete"]
)
def test_changed_selection_handles_empty_deleted_and_shared_paths(tmp_path, capsys, change):
    root = make_overlay(tmp_path, thin=True)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    if change == "untracked":
        make_overlay(root, thin=True, atom="dev-util/new")
        # make_overlay rewrites shared tracked bytes unchanged. Force the stale
        # stat condition instead of depending on Git's timestamp/racy-entry timing.
        for relative in ["metadata/layout.conf", "profiles/repo_name"]:
            rewrite_stat_only(root / relative)
    elif change == "cache-delete":
        (root / "metadata/md5-cache/dev-util/adapter-1").unlink()
    elif change == "eclass":
        (root / "eclass").mkdir()
        (root / "eclass/local.eclass").write_text("# shared\n")
        git(root, "add", "eclass")
    elif change == "package-delete":
        git(root, "rm", "-qr", "dev-util/adapter")
    status, report = run(root, capsys, "--changed-since", "HEAD")
    assert status == (1 if change == "cache-delete" else 0 if change == "eclass" else 2)
    assert report["examined_count"] == (1 if change in {"cache-delete", "eclass"} else 0)


def test_syntax_error_is_a_finding_and_dist_bytes_remain_unchecked(tmp_path, capsys):
    root = make_overlay(tmp_path, thin=True)
    ebuild = root / "dev-util/adapter/adapter-1.ebuild"
    ebuild.write_text(HEADER + "EAPI=8\nif then\n")
    (root / "metadata/md5-cache/dev-util/adapter-1").write_text(
        "EAPI=8\n_md5_=" + hashlib.md5(ebuild.read_bytes()).hexdigest() + "\n"
    )
    (root / "dev-util/adapter/Manifest").write_text("DIST not-fetched.tar.gz 42 SHA512 00\n")
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 1
    assert report["coverage"]["bash_syntax"] == "checked"
    assert report["package_coverage"]["dev-util/adapter"]["bash_syntax"] == "checked"
    assert any(item["check"] == "bash_syntax" for item in report["findings"])
    assert not any(item["check"] == "manifest_local" for item in report["findings"])
    assert report["coverage"]["remote_dist"] == "unchecked"


def test_human_output_explains_limited_coverage(tmp_path, capsys):
    root = make_overlay(tmp_path, thin=True)
    status = qa_ebuild.main(["--overlay-path", str(root), "--all"])
    output = capsys.readouterr().out
    assert status == 0
    assert "PASS: examined 1 package(s)" in output
    assert "thin-manifests = true" in output
    assert "Remote DIST bytes and Bash-expanded metadata are unchecked" in output


def test_xml_does_not_resolve_external_entity(tmp_path, capsys):
    root = make_overlay(tmp_path / "repo", thin=True)
    external = tmp_path / "secret"
    external.write_text("secret data")
    (root / "dev-util/adapter/metadata.xml").write_text(
        f'<!DOCTYPE pkgmetadata [<!ENTITY secret SYSTEM "{external.as_uri()}">]>'
        "<pkgmetadata>&secret;</pkgmetadata>"
    )
    status, report = run(root, capsys, "dev-util/adapter")
    assert status == 1
    assert any(item["check"] == "metadata_xml" for item in report["findings"])
    assert "secret data" not in json.dumps(report)


def test_changed_selection_ignores_inherited_git_checkout_overrides(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    decoy = make_overlay(tmp_path / "decoy", thin=True, atom="dev-util/decoy")
    for tree in [root, decoy]:
        git(tree, "init", "-q")
        git(tree, "add", ".")
        git(
            tree,
            "-c",
            "user.name=QA",
            "-c",
            "user.email=qa@example.invalid",
            "commit",
            "-qm",
            "base",
        )
    base = git(root, "rev-parse", "HEAD")
    (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    before_root, before_decoy = snapshot(root), snapshot(decoy)
    launcher = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", base, "--json"],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy / ".git/index"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["packages"] == ["dev-util/adapter"]
    assert snapshot(root) == before_root
    assert snapshot(decoy) == before_decoy


@pytest.mark.parametrize("configuration", ["host-files", "inherited-config"])
def test_changed_selection_ignores_host_git_configuration(tmp_path, configuration):
    root = make_overlay(tmp_path / "repo", thin=True)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if configuration == "host-files":
        home = tmp_path / "home"
        (home / ".config/git").mkdir(parents=True)
        for path in [home / ".gitconfig", home / ".config/git/config", home / "systemconfig"]:
            path.write_text("this is deliberately invalid Git configuration\n")
        environment.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "GIT_CONFIG_SYSTEM": str(home / "systemconfig"),
            }
        )
    else:
        environment.update({"GIT_CONFIG_COUNT": "invalid", "GIT_CONFIG_PARAMETERS": "bad"})
    launcher = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", base, "--json"],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["packages"] == ["dev-util/adapter"]


def test_missing_partial_repository_base_never_launches_fetch(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "head")
    sentinel = tmp_path / "fetch-launched"
    helper = tmp_path / "remote-helper"
    helper.write_text(f"#!/bin/bash\nprintf fetched > {sentinel}\nexit 1\n")
    helper.chmod(0o755)
    git(root, "config", "core.repositoryformatversion", "1")
    git(root, "config", "extensions.partialClone", "origin")
    git(root, "config", "remote.origin.promisor", "true")
    git(root, "config", "remote.origin.partialclonefilter", "blob:none")
    git(root, "config", "remote.origin.url", f"ext::{helper}")
    git(root, "config", "protocol.ext.allow", "always")
    (root / ".git/objects" / base[:2] / base[2:]).unlink()
    before = snapshot(root)
    launcher = Path(__file__).resolve().parents[1] / "bin/qa-ebuild"
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", base, "--json"],
        text=True,
        capture_output=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "INCONCLUSIVE"
    assert not sentinel.exists(), "Missing objects must not trigger a promisor remote fetch"
    assert snapshot(root) == before


def initialize_git_fixture(root: Path) -> str:
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    return git(root, "rev-parse", "HEAD")


def add_gitlink_fixture(parent: Path, child: Path, head: str) -> None:
    relative = child.relative_to(parent).as_posix()
    git(parent, "update-index", "--add", "--cacheinfo", f"160000,{head},{relative}")
    with (parent / ".gitmodules").open("a") as stream:
        stream.write(f'[submodule "{relative}"]\n\tpath = {relative}\n\turl = ./fixture\n')
    git(parent, "add", ".gitmodules")
    git(
        parent, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "link"
    )


@pytest.mark.parametrize("storage", ["directory", "gitfile"])
@pytest.mark.parametrize("depth", [1, 2], ids=["child", "grandchild"])
@pytest.mark.parametrize("configuration", ["local", "include", "worktree"])
@pytest.mark.parametrize("driver", ["clean", "process"])
@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
def test_public_changed_selection_preflights_submodule_filters(
    tmp_path, storage, depth, configuration, driver, name
):
    root = make_overlay(tmp_path / "repo", thin=True)
    initialize_git_fixture(root)
    parent = root
    children = []
    for _ in range(depth):
        child = parent / "support/sub"
        child.mkdir(parents=True)
        (child / "payload").write_bytes(b"alpha\n")
        (child / ".gitattributes").write_text("payload filter=probe\n")
        head = initialize_git_fixture(child)
        add_gitlink_fixture(parent, child, head)
        children.append(child)
        parent = child
    # Link the changed child commit upward without converting filtered files.
    for parent, child in reversed(list(zip([root, *children[:-1]], children, strict=True))):
        add_gitlink_fixture(parent, child, git(child, "rev-parse", "HEAD"))
    if storage == "gitfile":
        for index, submodule in enumerate(children):
            gitdir = root / f".git/modules/fixture-{index}"
            gitdir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(submodule / ".git"), str(gitdir))
            (submodule / ".git").write_text(f"gitdir: {gitdir}\n")
    child = children[-1]
    marker = tmp_path / "SUBMODULE_FILTER_EXECUTED"
    helper = tmp_path / "filter-probe"
    helper.write_text(
        f"#!/bin/bash\nprintf executed > {str(marker)!r}\n"
        + ("exec /bin/cat\n" if driver == "clean" else "exit 1\n")
    )
    helper.chmod(0o755)
    if configuration == "include":
        config = tmp_path / "filter.config"
        git(child, "config", "--file", str(config), f"filter.probe.{driver}", str(helper))
        git(child, "config", "include.path", str(config))
    elif configuration == "worktree":
        git(child, "config", "extensions.worktreeConfig", "true")
        git(child, "config", "--worktree", f"filter.probe.{driver}", str(helper))
    else:
        git(child, "config", f"filter.probe.{driver}", str(helper))
    (child / "payload").write_bytes(b"bravo\n")
    launcher = public_launcher(tmp_path, name)
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", "HEAD"]
        + (["--json"] if name == "qa-ebuild" else []),
        capture_output=True,
        text=True,
        check=False,
    )
    assert not marker.exists(), "Git inspected a populated submodule before safety preflight"
    assert result.returncode == 2, result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["coverage"]["selection"] == "unchecked"
        assert report["examined_count"] == report["selected_count"] == 0
        assert report["package_coverage"] == {}
    assert "filter" in result.stdout
    assert "not applicable" not in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("state", ["dirty", "unpopulated", "malformed", "wrong-root", "escape"])
def test_public_changed_selection_validates_children_without_changing_package_mapping(
    tmp_path, name, state
):
    root = make_overlay(tmp_path / "repo", thin=True)
    initialize_git_fixture(root)
    child = root / "support/sub"
    child.mkdir(parents=True)
    (child / "payload").write_bytes(b"alpha\n")
    add_gitlink_fixture(root, child, initialize_git_fixture(child))
    if state == "dirty":
        (child / "payload").write_bytes(b"bravo\n")
    elif state == "unpopulated":
        shutil.rmtree(child)
    elif state == "malformed":
        shutil.rmtree(child / ".git")
        (child / ".git").write_text("not a gitdir\n")
    elif state == "wrong-root":
        git(child, "config", "core.worktree", str(root))
    else:
        outside = tmp_path / "outside"
        shutil.move(str(child), str(outside))
        child.symlink_to(outside, target_is_directory=True)
    (root / "dev-util/adapter/files/adapter.patch").write_bytes(b"other patch\n")
    launcher = public_launcher(tmp_path, name)
    before = snapshot(root)
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), "--changed-since", "HEAD"]
        + (["--json"] if name == "qa-ebuild" else []),
        capture_output=True,
        text=True,
        check=False,
    )
    safe = state in {"dirty", "unpopulated"}
    assert result.returncode == (0 if safe else 2), result.stdout + result.stderr
    if name == "qa-ebuild":
        report = json.loads(result.stdout)
        assert report["coverage"]["selection"] == (
            "git-base-plus-index-and-worktree" if safe else "unchecked"
        )
        assert report["packages"] == (["dev-util/adapter"] if safe else [])
    else:
        assert ("PASS: examined 1" if safe else "INCONCLUSIVE: examined 0") in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert snapshot(root) == before


@pytest.mark.parametrize("name", ["qa-ebuild", "qa-ebuild-check"])
@pytest.mark.parametrize("provisioned", [True, False], ids=["prepared", "missing-venv"])
def test_public_launchers_ignore_environment_startup_code(tmp_path, name, provisioned):
    root = make_overlay(tmp_path / "repo", thin=True)
    launcher = public_launcher(tmp_path, name)
    if not provisioned:
        (launcher.parent.parent / ".venv").unlink()
    marker = tmp_path / "STARTUP_EXECUTED"
    startup = tmp_path / "startup.sh"
    startup.write_text(f"printf executed > {str(marker)!r}\n")
    # The shebang's host Python and the re-exec both run under this test version.
    host = tmp_path / "host-bin"
    host.mkdir()
    (host / "python3").symlink_to(sys.executable)
    (tmp_path / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'w').write('sitecustomize')\n"
    )
    environment = {
        **os.environ,
        "BASH_ENV": str(startup),
        "ENV": str(startup),
        "PYTHONPATH": str(tmp_path),
        "PYTHONHOME": str(tmp_path / "invalid-home"),
        "PATH": str(host) + os.pathsep + os.environ["PATH"],
    }
    before = snapshot(root)
    arguments = ["--all", "--json"] if name == "qa-ebuild" else ["--initial"]
    result = subprocess.run(
        [str(launcher), "--overlay-path", str(root), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not marker.exists(), "Public executable ran caller environment startup code"
    assert result.returncode == (0 if provisioned else 2), result.stdout + result.stderr
    assert ("PASS" if provisioned else "INCONCLUSIVE") in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert snapshot(root) == before
