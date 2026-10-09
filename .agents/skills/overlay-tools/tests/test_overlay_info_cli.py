"""Generated checkout context tested only through overlay-info's public CLI."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def make_overlay(root: Path, *, name: str = "fixture-overlay") -> Path:
    (root / "profiles").mkdir(parents=True)
    (root / "profiles/repo_name").write_text(name + "\n")
    (root / "metadata").mkdir()
    (root / "metadata/layout.conf").write_text("masters = gentoo\n")
    for atom, versions in {"dev-util/editor": ["1", "2"], "net-im/chat": ["3"]}.items():
        package = root / atom
        package.mkdir(parents=True)
        for version in versions:
            (package / f"{package.name}-{version}.ebuild").write_text("EAPI=8\n")
    return root


def invoke(root: Path, capsys, *flags: str):
    from overlay_tools.cli.overlay_info import main

    code = main(["--overlay-path", str(root), *flags])
    return code, capsys.readouterr()


def test_json_reports_actual_inventory_and_configuration(tmp_path, capsys):
    root = make_overlay(tmp_path)

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    assert output.err == ""
    data = json.loads(output.out)
    assert data["schema_version"] == 1
    assert data["mode"] == "summary"
    assert data["checkout"]["root"] == str(root.resolve())
    assert data["checkout"]["name"] == "fixture-overlay"
    assert data["inventory"]["category_count"] == 2
    assert data["inventory"]["package_count"] == 2
    assert data["inventory"]["ebuild_count"] == 3
    assert data["inventory"]["packages"] == {
        "total": 2,
        "truncated": 0,
        "items": [
            {"atom": "dev-util/editor", "ebuild_count": 2},
            {"atom": "net-im/chat", "ebuild_count": 1},
        ],
    }
    assert data["configuration"]["masters"]["items"] == ["gentoo"]
    assert data["configuration"]["eapi"]["ebuilds"]["items"] == [{"value": "8", "count": 3}]
    assert data["configuration"]["eapi"]["profile"] is None
    assert data["configuration"]["manifest"]["thin_manifests"] is None
    assert "full_configuration" not in data
    assert "pkgcheck scan ." in [item["command"] for item in data["verification"]]
    assert any(
        item["command"].startswith(".agents/skills/overlay-tools/bin/test-ebuild --overlay-path ")
        and item["cwd"] == str(root)
        for item in data["verification"]
    )


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        ("profiles/repo_name", ""),
        ("profiles/repo_name", "two names\n"),
        ("profiles/repo_name", "bad/name\n"),
        ("profiles/eapi", "not-an-eapi\n"),
        ("metadata/layout.conf", "masters gentoo\n"),
        ("metadata/layout.conf", "masters = gentoo\nmasters = other\n"),
        ("metadata/layout.conf", "thin-manifests = perhaps\n"),
        ("metadata/layout.conf", "use-manifests = perhaps\n"),
        ("metadata/layout.conf", " = gentoo\n"),
        ("metadata/layout.conf", "masters = ../bad\n"),
    ],
)
def test_invalid_configuration_fails_without_partial_json(tmp_path, capsys, relative_path, content):
    root = make_overlay(tmp_path)
    (root / relative_path).write_text(content)

    code, output = invoke(root, capsys, "--json")

    assert code == 2
    assert output.out == ""
    assert relative_path in output.err


@pytest.mark.parametrize("relative", ["absent", "profiles/repo_name"])
def test_invalid_explicit_start_is_not_rescued_by_parent_overlay(tmp_path, capsys, relative):
    root = make_overlay(tmp_path)

    code, output = invoke(root / relative, capsys, "--json")

    assert code == 2
    assert output.out == ""
    assert "valid Gentoo overlay" in output.err


@pytest.mark.parametrize("selection", ["explicit", "cwd"])
@pytest.mark.parametrize(
    "kind", ["broken-symlink", "directory", "invalid-utf8", "unreadable", "symlink-loop"]
)
def test_nearest_invalid_marker_is_not_rescued_by_ancestor(
    tmp_path, monkeypatch, capsys, selection, kind
):
    parent = make_overlay(tmp_path / "parent", name="parent-overlay")
    nested = parent / "nested"
    (nested / "profiles").mkdir(parents=True)
    marker = nested / "profiles/repo_name"
    if kind == "broken-symlink":
        marker.symlink_to(nested / "missing")
    elif kind == "directory":
        marker.mkdir()
    elif kind == "invalid-utf8":
        marker.write_bytes(b"\xff")
    elif kind == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("Root bypasses read permissions")
        marker.write_text("nested-overlay\n")
        marker.chmod(0)
    else:
        marker.symlink_to(marker)
    start = nested / "deep/inside"
    start.mkdir(parents=True)

    if selection == "explicit":
        code, output = invoke(start, capsys, "--json")
    else:
        from overlay_tools.cli.overlay_info import main

        monkeypatch.chdir(start)
        code = main(["--json"])
        output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert str(marker) in output.err


def test_symlink_loop_root_is_an_exit_two_error(tmp_path, capsys):
    root = tmp_path / "loop"
    root.symlink_to(root)

    code, output = invoke(root, capsys, "--json")

    assert code == 2
    assert output.out == ""
    assert "loop" in output.err


def test_explicit_root_overrides_cwd_and_same_name_checkouts(tmp_path, monkeypatch, capsys):
    first = make_overlay(tmp_path / "first")
    second = make_overlay(tmp_path / "second")
    monkeypatch.chdir(first / "dev-util/editor")

    code, output = invoke(second / "net-im", capsys, "--json")

    assert code == 0
    assert json.loads(output.out)["checkout"]["root"] == str(second.resolve())
    from overlay_tools.cli.overlay_info import main

    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["checkout"]["root"] == str(first.resolve())
    assert data["checkout"]["name"] == "fixture-overlay"


def test_summary_bounds_every_growing_list_and_full_restores_data(tmp_path, capsys):
    root = make_overlay(tmp_path)
    exclusions = {}
    (root / "eclass").mkdir()
    for index in range(35):
        atom = f"cat-{index:02}/tool"
        package = root / atom
        package.mkdir(parents=True)
        (package / "tool-1.ebuild").write_text("EAPI=8\n")
        (root / f"eclass/local-{index:02}.eclass").write_text("# local\n")
        exclusions[atom] = f"Paused {index}: " + "x" * 500
    exclusions["gone/old"] = "Retired, no longer present."
    (root / "metadata/update-exclusions.json").write_text(json.dumps(exclusions))
    layout = "masters = " + " ".join(f"master-{index}" for index in range(35)) + "\n"
    layout += "thin-manifests = true\nuse-manifests = strict\n"
    layout += "manifest-hashes = BLAKE2B SHA512\nmanifest-required-hashes = SHA512\n"
    layout += "cache-formats = md5-dict\n"
    (root / "metadata/layout.conf").write_text(layout)
    (root / "profiles/eapi").write_text("8\n")
    (root / "profiles/package.mask").write_text("# Only shown with --full\ncat-34/tool\n")

    code, output = invoke(root, capsys, "--json")
    data = json.loads(output.out)
    assert code == 0
    assert data["limits"] == {
        "items": 10,
        "reason_characters": 240,
        "string_characters": 240,
        "output_bytes": 131_072,
    }
    assert data["inventory"]["category_count"] == 37
    assert data["inventory"]["package_count"] == 37
    assert data["inventory"]["ebuild_count"] == 38
    for group, total in [
        (data["inventory"]["categories"], 37),
        (data["inventory"]["packages"], 37),
        (data["configuration"]["masters"], 35),
        (data["local_eclasses"], 35),
        (data["update_policy"]["exclusions"], 36),
        (data["update_policy"]["active_exclusions"], 35),
    ]:
        assert group["total"] == total
        assert len(group["items"]) == 10
        assert group["truncated"] == total - 10
    assert data["update_policy"]["path"] == str(root / "metadata/update-exclusions.json")
    assert data["update_policy"]["active_count"] == 35
    reason = data["update_policy"]["exclusions"]["items"][0]
    assert len(reason["reason"]) == 240
    assert reason["reason_characters"] == 510
    assert reason["reason_truncated"] == 270
    assert "Only shown" not in output.out
    assert "cache-formats" not in output.out
    assert "full_configuration" not in data
    assert "ebuilds" not in data["inventory"]
    assert data["configuration"]["manifest"]["hashes"]["items"] == ["BLAKE2B", "SHA512"]
    assert data["configuration"]["manifest"]["use_manifests"] == "strict"

    code, output = invoke(root, capsys)
    assert code == 0
    assert "Overlay context" in output.out
    assert "37 total, 27 truncated" in output.out
    assert "local-34" not in output.out
    assert "cat-34/tool" not in output.out

    code, output = invoke(root, capsys, "--json", "--full")
    full = json.loads(output.out)
    assert code == 0
    assert full["mode"] == "full"
    assert full["inventory"]["packages"]["total"] == 37
    assert len(full["inventory"]["packages"]["items"]) == 37
    assert full["inventory"]["packages"]["truncated"] == 0
    assert full["inventory"]["ebuilds"]["total"] == 38
    assert len(full["local_eclasses"]["items"]) == 35
    assert full["update_policy"]["exclusions"]["items"][-1]["active"] is False
    assert full["update_policy"]["exclusions"]["items"][0]["reason"] == exclusions["cat-00/tool"]
    assert full["full_configuration"]["metadata/layout.conf"] == layout
    assert "Only shown" in full["full_configuration"]["profiles/package.mask"]
    assert full["configuration"]["eapi"]["profile"] == "8"


@pytest.mark.parametrize("hash_token", ["X" * 1_000_000, "😀" * 1_000], ids=["ascii", "unicode"])
def test_summary_bounds_massive_identifiers_and_counts_omitted_codepoints(
    tmp_path, capsys, hash_token
):
    root = make_overlay(tmp_path)
    atom = "cat/" + "a" * 1_000_000
    reason = "😀" * 1_001
    (root / "metadata/update-exclusions.json").write_text(json.dumps({atom: reason}))
    (root / "metadata/layout.conf").write_text(
        "manifest-hashes = " + " ".join([hash_token] * 35) + "\n"
        "manifest-required-hashes = " + hash_token + "\n"
    )

    code, output = invoke(root, capsys, "--json")
    assert code == 0
    assert len(output.out.encode("utf-8")) <= 131_072
    data = json.loads(output.out)
    assert data["limits"]["string_characters"] == 240
    assert data["limits"]["output_bytes"] == 131_072
    item = data["update_policy"]["exclusions"]["items"][0]
    assert item["atom"] == atom[:240]
    assert item["reason"] == "😀" * 240
    assert item["reason_characters"] == 1_001
    assert item["reason_truncated"] == 761
    truncated = {entry["path"]: entry for entry in data["string_truncations"]}
    assert truncated["/update_policy/exclusions/items/0/atom"] == {
        "path": "/update_policy/exclusions/items/0/atom",
        "characters": 1_000_004,
        "truncated": 999_764,
    }
    assert truncated["/update_policy/exclusions/items/0/reason"]["truncated"] == 761
    assert truncated["/configuration/manifest/hashes/items/0"]["truncated"] == (
        999_760 if hash_token.startswith("X") else 760
    )
    hashes = data["configuration"]["manifest"]["hashes"]
    assert hashes["total"] == 35
    assert hashes["truncated"] == 25
    assert hashes["items"] == [hash_token[:240]] * 10

    code, output = invoke(root, capsys)
    assert code == 0
    assert len(output.out.encode("utf-8")) <= 131_072
    assert "string truncations" in output.out

    code, output = invoke(root, capsys, "--json", "--full")
    assert code == 0
    data = json.loads(output.out)
    assert data["string_truncations"] == []
    assert data["limits"]["output_bytes"] is None
    assert data["update_policy"]["exclusions"]["items"][0]["atom"] == atom
    assert data["update_policy"]["exclusions"]["items"][0]["reason"] == reason
    assert data["configuration"]["manifest"]["hashes"]["items"] == [hash_token] * 35


def test_summary_bounds_paths_and_names_without_claiming_complete_identifiers(tmp_path, capsys):
    root = make_overlay(tmp_path / ("r" * 120) / ("s" * 120))
    category = "c" * 245
    package_name = "p" * 245
    package = root / category / package_name
    package.mkdir(parents=True)
    (package / "tool-1.ebuild").write_text("EAPI=8\n")
    (root / "eclass").mkdir()
    (root / "eclass" / ("e" * 240 + ".eclass")).write_text("# local\n")

    code, output = invoke(root, capsys, "--json")
    assert code == 0
    summary = json.loads(output.out)
    assert summary["checkout"]["root"] == str(root.resolve())[:240]
    assert summary["checkout"]["root"] != str(root.resolve())
    assert len(output.out.encode("utf-8")) <= 131_072
    code, output = invoke(root, capsys, "--json", "--full")
    assert code == 0
    full = json.loads(output.out)
    assert full["checkout"]["root"] == str(root.resolve())

    def strings(value, pointer=""):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from strings(child, f"{pointer}/{key.replace('~', '~0').replace('/', '~1')}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from strings(child, f"{pointer}/{index}")
        elif isinstance(value, str):
            yield pointer, value

    complete_strings = dict(strings(full))
    truncations = {entry["path"]: entry for entry in summary["string_truncations"]}
    assert "/checkout/root" in truncations
    assert "/inventory/categories/items/0/name" in truncations
    assert "/inventory/packages/items/0/atom" in truncations
    assert "/local_eclasses/items/0" in truncations
    assert "/verification/2/command" in truncations
    for pointer, value in strings(summary):
        if pointer.startswith("/string_truncations/"):
            continue
        assert len(value) <= 240
        original = complete_strings[pointer]
        if len(original) > 240:
            assert value == original[:240]
            assert truncations[pointer]["characters"] == len(original)
            assert truncations[pointer]["truncated"] == len(original) - 240


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_summary_budget_overflow_fails_without_partial_output(tmp_path, capsys, flags):
    root = make_overlay(tmp_path.joinpath(*["😀" * 50] * 6))
    for index in range(10):
        package = root / f"cat/tool-p{index:02}"
        package.mkdir(parents=True)
        (package / f"{package.name}-1.ebuild").write_text("EAPI=8\n")
    reason = "😀" * 1_000
    (root / "metadata/update-exclusions.json").write_text(
        json.dumps({f"cat/tool-p{index:02}": reason for index in range(10)})
    )
    token = "😀" * 1_000
    hashes = " ".join([token] * 10)
    (root / "metadata/layout.conf").write_text(
        f"manifest-hashes = {hashes}\nmanifest-required-hashes = {hashes}\n"
    )
    code, output = invoke(root, capsys, *flags)
    assert code == 2
    assert output.out == ""
    assert "131072-byte output budget" in output.err
    code, output = invoke(root, capsys, *flags, "--full")
    assert code == 0
    assert len(output.out.encode("utf-8")) > 131_072


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def initialize_git(root: Path) -> str:
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return git(root, "rev-parse", "HEAD")


def test_git_revision_and_dirty_state_refresh_without_writes(tmp_path, capsys):
    root = make_overlay(tmp_path)
    revision = initialize_git(root)
    index_before = (root / ".git/index").read_bytes()

    code, output = invoke(root, capsys, "--json")
    checkout = json.loads(output.out)["checkout"]
    assert code == 0
    assert checkout["git"] == {
        "status": "checked",
        "revision": revision,
        "dirty": False,
        "reason": None,
    }
    assert (root / ".git/index").read_bytes() == index_before
    (root / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")
    (root / "untracked").write_text("new\n")

    code, output = invoke(root, capsys, "--json")
    data = json.loads(output.out)
    assert code == 0
    assert data["checkout"]["git"]["revision"] == revision
    assert data["checkout"]["git"]["dirty"] is True
    assert data["configuration"]["eapi"]["ebuilds"]["items"] == [
        {"value": "7", "count": 1},
        {"value": "8", "count": 2},
    ]
    assert (root / ".git/index").read_bytes() == index_before
    assert not (root / ".cache").exists()


def test_dirty_state_includes_submodule_worktree_changes_without_fetching(tmp_path, capsys):
    root = make_overlay(tmp_path)
    child = make_overlay(root / ".vendor")
    initialize_git(child)
    (root / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = .vendor\n\turl = ../unused-local-path\n'
    )
    initialize_git(root)
    code, output = invoke(root, capsys, "--json")
    assert code == 0
    assert json.loads(output.out)["checkout"]["git"]["dirty"] is False
    (child / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    assert json.loads(output.out)["checkout"]["git"]["dirty"] is True


@pytest.mark.parametrize("state", ["not-a-checkout", "git-missing", "unborn", "nested"])
def test_git_unchecked_never_fabricates_revision_or_clean_state(
    tmp_path, monkeypatch, capsys, state
):
    root = make_overlay(tmp_path / "overlay")
    if state == "git-missing":
        monkeypatch.setenv("PATH", "")
    elif state == "unborn":
        git(root, "init", "--quiet")
    elif state == "nested":
        initialize_git(tmp_path)

    code, output = invoke(root, capsys, "--json")
    report = json.loads(output.out)["checkout"]["git"]
    assert code == 0
    assert report["status"] == "unchecked"
    assert report["revision"] is None
    assert report["dirty"] is None
    assert report["reason"]


def test_git_does_not_run_checkout_fsmonitor_hook_or_follow_git_environment(
    tmp_path, monkeypatch, capsys
):
    root = make_overlay(tmp_path / "selected")
    expected = initialize_git(root)
    other = make_overlay(tmp_path / "other")
    initialize_git(other)
    marker = tmp_path / "executed"
    hook = root / ".git/fsmonitor-hook"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    git(root, "config", "core.fsmonitor", str(hook))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    assert json.loads(output.out)["checkout"]["git"]["revision"] == expected
    assert not marker.exists()


@pytest.mark.parametrize("filter_kind", ["clean", "process"])
@pytest.mark.parametrize("config_source", ["local", "include", "conditional-include", "worktree"])
@pytest.mark.parametrize("full", [False, True])
def test_git_executable_filters_are_unchecked_without_execution_or_repository_writes(
    tmp_path, capsys, filter_kind, config_source, full
):
    root = make_overlay(tmp_path / "overlay")
    marker = tmp_path / "filter-executed"
    script = root / "checkout-filter"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    (root / ".gitattributes").write_text("*.ebuild filter=probe\n")
    initialize_git(root)
    key = f"filter.probe.{filter_kind}"
    if config_source == "local":
        git(root, "config", key, str(script))
    elif config_source == "worktree":
        git(root, "config", "extensions.worktreeConfig", "true")
        git(root, "config", "--worktree", key, str(script))
    else:
        config = root / ".git/filter-config"
        config.write_text(f'[filter "probe"]\n\t{filter_kind} = {script}\n')
        include_key = (
            "include.path"
            if config_source == "include"
            else f"includeIf.gitdir:{root / '.git'}.path"
        )
        git(root, "config", include_key, str(config))
    # Equal size changes still require Git to compare content and invoke filters.
    (root / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")
    before = {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    flags = ["--json", "--full"] if full else ["--json"]
    code, output = invoke(root, capsys, *flags)

    assert not marker.exists()
    assert code == 0
    assert output.err == ""
    assert json.loads(output.out)["checkout"]["git"] == {
        "status": "unchecked",
        "revision": None,
        "dirty": None,
        "reason": "Git inspection skipped: executable clean/process filter configured",
    }
    assert before == {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_git_filter_commands_from_inherited_environment_and_host_configs_are_ignored(
    tmp_path, monkeypatch, capsys
):
    root = make_overlay(tmp_path / "overlay")
    (root / ".gitattributes").write_text("*.ebuild filter=probe\n")
    revision = initialize_git(root)
    marker = tmp_path / "inherited-filter-executed"
    script = tmp_path / "host-filter"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    config = tmp_path / "host-config"
    config.write_text(f'[filter "probe"]\n\tclean = {script}\n\tprocess = {script}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.probe.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(script))
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "filter.probe.process")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", str(script))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'filter.probe.clean={script}'")
    (root / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")
    index_before = (root / ".git/index").read_bytes()

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    assert not marker.exists()
    assert json.loads(output.out)["checkout"]["git"] == {
        "status": "checked",
        "revision": revision,
        "dirty": True,
        "reason": None,
    }
    assert (root / ".git/index").read_bytes() == index_before


@pytest.mark.parametrize("filter_kind", ["clean", "process"])
def test_empty_git_filter_commands_do_not_prevent_safe_dirty_inspection(
    tmp_path, capsys, filter_kind
):
    root = make_overlay(tmp_path)
    (root / ".gitattributes").write_text("*.ebuild filter=probe\n")
    revision = initialize_git(root)
    git(root, "config", f"filter.probe.{filter_kind}", "")
    (root / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    assert json.loads(output.out)["checkout"]["git"] == {
        "status": "checked",
        "revision": revision,
        "dirty": True,
        "reason": None,
    }


def test_overridden_git_filter_command_is_still_refused_in_full_text(tmp_path, capsys):
    root = make_overlay(tmp_path)
    initialize_git(root)
    # Neither unused attributes nor a later empty value permit unsafe inspection.
    git(root, "config", "filter.unused.clean", "exit 99")
    git(root, "config", "--add", "filter.unused.clean", "")

    code, output = invoke(root, capsys, "--full")

    assert code == 0
    assert 'status: "unchecked"' in output.out
    assert "revision: null" in output.out
    assert "dirty: null" in output.out
    assert (
        'reason: "Git inspection skipped: executable clean/process filter configured"' in output.out
    )


@pytest.mark.parametrize("filter_kind", ["clean", "process"])
@pytest.mark.parametrize("depth", [1, 2])
def test_submodule_filters_are_also_unchecked_before_content_comparison(
    tmp_path, capsys, filter_kind, depth
):
    root = make_overlay(tmp_path / "overlay")
    repositories = [root]
    for _ in range(depth):
        repositories.append(make_overlay(repositories[-1] / ".vendor"))
        (repositories[-2] / ".gitmodules").write_text(
            '[submodule "vendor"]\n\tpath = .vendor\n\turl = ../unused-local-path\n'
        )
    child = repositories[-1]
    marker = tmp_path / "submodule-filter-executed"
    script = child / "checkout-filter"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    (child / ".gitattributes").write_text("*.ebuild filter=probe\n")
    for repository in reversed(repositories):
        initialize_git(repository)
    git(child, "config", f"filter.probe.{filter_kind}", str(script))
    (child / "net-im/chat/chat-3.ebuild").write_text("EAPI=7\n")
    before = {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    code, output = invoke(root, capsys, "--json")

    assert not marker.exists()
    assert code == 0
    assert json.loads(output.out)["checkout"]["git"] == {
        "status": "unchecked",
        "revision": None,
        "dirty": None,
        "reason": "Git inspection skipped: executable clean/process filter configured",
    }
    assert before == {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_eapi_is_literal_metadata_and_never_executes_ebuild_or_eclass(tmp_path, capsys):
    root = make_overlay(tmp_path)
    marker = tmp_path / "executed"
    (root / "dev-util/editor/editor-1.ebuild").write_text(f"EAPI=$(touch '{marker}')\n")
    (root / "dev-util/editor/editor-2.ebuild").write_text(
        '# Header\nEAPI="8" # literal\nsrc_prepare() { EAPI=7; }\n'
    )
    (root / "net-im/chat/chat-3.ebuild").write_text("# Missing declaration\nDESCRIPTION=chat\n")
    (root / "eclass").mkdir()
    (root / "eclass/local.eclass").write_text(f"touch '{marker}'\n")
    (root / "metadata/layout.conf").write_text(
        "masters = gentoo\neapis-banned = 0 1 2\neapis-deprecated = 3 4\n"
        "eapis-testing = 9\nprofile-eapis-banned = 0\nprofile-eapis-deprecated = 1\n"
        "sign-manifests = false\n"
    )

    code, output = invoke(root, capsys, "--json")
    data = json.loads(output.out)
    assert code == 0
    assert data["configuration"]["eapi"]["ebuilds"]["items"] == [{"value": "8", "count": 1}]
    assert data["configuration"]["eapi"]["unresolved_count"] == 2
    assert data["configuration"]["eapi"]["banned"]["items"] == ["0", "1", "2"]
    assert data["configuration"]["eapi"]["deprecated"]["items"] == ["3", "4"]
    assert data["configuration"]["eapi"]["testing"]["items"] == ["9"]
    assert data["configuration"]["manifest"]["sign_manifests"] == "false"
    assert data["local_eclasses"]["items"] == ["local.eclass"]
    assert not marker.exists()
    assert "touch" not in output.out


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        '{"cat/pkg": ""}',
        '{"cat/pkg": 1}',
        '{"cat/pkg": "one", "cat/pkg": "two"}',
        '{"cat/pkg-1": "bad atom"}',
    ],
)
def test_invalid_update_policy_fails_honestly(tmp_path, capsys, content):
    root = make_overlay(tmp_path)
    (root / "metadata/update-exclusions.json").write_text(content)

    code, output = invoke(root, capsys, "--json")

    assert code == 2
    assert output.out == ""
    assert "Invalid update policy" in output.err


@pytest.mark.parametrize(
    "relative",
    [
        "profiles/repo_name",
        "profiles/eapi",
        "metadata/layout.conf",
        "metadata/update-exclusions.json",
    ],
)
@pytest.mark.parametrize("kind", ["directory", "invalid-utf8", "broken-symlink"])
def test_unreadable_configuration_or_policy_is_not_missing(tmp_path, capsys, relative, kind):
    root = make_overlay(tmp_path)
    path = root / relative
    path.unlink(missing_ok=True)
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    else:
        path.symlink_to(root / "absent")

    code, output = invoke(root, capsys, "--json")

    assert code == 2
    assert output.out == ""
    assert relative in output.err


def test_missing_optional_configuration_and_policy_are_explicit(tmp_path, capsys):
    root = make_overlay(tmp_path)
    (root / "metadata/layout.conf").unlink()

    code, output = invoke(root, capsys, "--json")
    data = json.loads(output.out)

    assert code == 0
    assert data["configuration"]["layout_present"] is False
    assert data["configuration"]["masters"] == {"items": [], "total": 0, "truncated": 0}
    assert data["update_policy"]["present"] is False
    assert data["update_policy"]["active_count"] == 0
    assert data["update_policy"]["exclusions"] == {"items": [], "total": 0, "truncated": 0}


@pytest.mark.parametrize("flags", [[], ["--json"], ["--json", "--full"]])
def test_executable_wrapper_runs_public_cli_from_other_cwd(tmp_path, flags):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    root = make_overlay(tmp_path / "overlay")
    tools = Path(__file__).resolve().parents[1]
    wrapper = tools / "bin/overlay-info"

    result = subprocess.run(
        [str(wrapper), "--overlay-path", str(root), *flags],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if "--json" in flags:
        assert json.loads(result.stdout)["checkout"]["root"] == str(root)
    else:
        assert result.stdout.startswith("Overlay context\n")


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize("nested", [False, True], ids=["mask-root", "nested-directory"])
@pytest.mark.parametrize("mode", [0, stat.S_IRUSR, stat.S_IXUSR], ids=["none", "read", "search"])
def test_wrapper_full_rejects_unreadable_mask_directories_without_partial_output(
    tmp_path, flags, nested, mode
):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    if os.geteuid() == 0:
        pytest.skip("Root bypasses directory permissions")
    root = make_overlay(tmp_path / "overlay")
    mask = root / "profiles/package.mask"
    mask.mkdir()
    (mask / "visible").write_text("# Visible mask\nnet-im/chat\n")
    blocked = mask / "nested" if nested else mask
    blocked.mkdir(exist_ok=True)
    (blocked / "deprecated").write_text("# Hidden mask\ndev-util/editor\n")
    tools = Path(__file__).resolve().parents[1]
    command = [str(tools / "bin/overlay-info"), "--overlay-path", str(root), *flags]
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": sys.prefix, "UV_OFFLINE": "1"}
    original_mode = blocked.stat().st_mode
    try:
        blocked.chmod(mode)
        summary = subprocess.run(
            command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=10
        )
        assert summary.returncode == 0, summary.stderr
        assert summary.stderr == ""
        assert "Hidden mask" not in summary.stdout
        assert "full_configuration" not in summary.stdout
        assert "full configuration" not in summary.stdout
        full = subprocess.run(
            [*command, "--full"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert full.returncode == 2, full.stdout
        assert full.stdout == ""
        assert full.stderr.startswith("overlay-info: error: ")
        assert str(blocked) in full.stderr
        assert "Permission denied" in full.stderr
        assert "Traceback" not in full.stderr
        assert "Hidden mask" not in full.stderr
        assert "Visible mask" not in full.stderr
    finally:
        blocked.chmod(original_mode)

    recovered = subprocess.run(
        [*command, "--full"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stderr == ""
    assert "Hidden mask" in recovered.stdout
    assert "Visible mask" in recovered.stdout


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize("nested", [False, True], ids=["mask-root", "nested-link"])
@pytest.mark.parametrize("kind", ["directory", "broken", "loop"])
def test_wrapper_full_rejects_mask_directory_links_and_invalid_links(tmp_path, flags, nested, kind):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    root = make_overlay(tmp_path / "overlay")
    path = root / "profiles/package.mask"
    if nested:
        path.mkdir()
        (path / "visible").write_text("# Visible mask\nnet-im/chat\n")
        path = path / "linked"
    target = tmp_path / "outside"
    if kind == "directory":
        target.mkdir()
        (target / "confidential").write_text("# Outside configuration\ndev-util/editor\n")
    elif kind == "loop":
        target = path
    path.symlink_to(target)
    tools = Path(__file__).resolve().parents[1]
    command = [str(tools / "bin/overlay-info"), "--overlay-path", str(root), *flags]
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": sys.prefix, "UV_OFFLINE": "1"}

    summary = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=10
    )
    assert summary.returncode == 0, summary.stderr
    assert summary.stderr == ""
    full = subprocess.run(
        [*command, "--full"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert full.returncode == 2
    assert full.stdout == ""
    assert full.stderr.startswith("overlay-info: error: ")
    assert str(path) in full.stderr
    assert "Traceback" not in full.stderr
    assert "Outside configuration" not in full.stderr
    assert "Visible mask" not in full.stderr


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
def test_wrapper_full_reports_sorted_nested_masks_and_preserves_file_symlink_reads(tmp_path, flags):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    root = make_overlay(tmp_path / "overlay")
    mask = root / "profiles/package.mask"
    (mask / "z/deep").mkdir(parents=True)
    (mask / "a/empty").mkdir(parents=True)
    # Creation order differs from report order, including a hidden mask file.
    raw = {
        "profiles/package.mask/z/deep/last": b"# Last\r\nnet-im/chat\n\r",
        "profiles/package.mask/a/first": b"# First\ndev-util/editor\r\n",
        "profiles/package.mask/.hidden": b"# Hidden\rnet-im/chat\n",
    }
    for relative, content in raw.items():
        (root / relative).write_bytes(content)
    target = tmp_path / "mask-file"
    target.write_bytes(b"# Linked\r\ndev-util/editor\r")
    (mask / "link").symlink_to(target)
    raw["profiles/package.mask/link"] = b"# Linked\r\ndev-util/editor\r"
    tools = Path(__file__).resolve().parents[1]
    command = [str(tools / "bin/overlay-info"), "--overlay-path", str(root), *flags, "--full"]
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": sys.prefix, "UV_OFFLINE": "1"}
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if "--json" in flags:
        actual = {
            relative: content.encode("utf-8")
            for relative, content in json.loads(result.stdout)["full_configuration"].items()
            if relative.startswith("profiles/package.mask/")
        }
    else:
        actual = {}
        for line in result.stdout.splitlines():
            relative, separator, content = line.strip().partition(": ")
            if separator and relative.startswith("profiles/package.mask/"):
                actual[relative] = json.loads(content).encode("utf-8")
    assert list(actual) == sorted(raw)
    assert actual == raw


@pytest.mark.parametrize("from_overlay", [False, True])
def test_wrapper_ignores_cwd_modules_and_python_startup_injection(tmp_path, from_overlay):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    root = make_overlay(tmp_path / "overlay")
    injected = tmp_path / "pythonpath"
    injected.mkdir()
    markers = []
    for location in [root, injected]:
        package = location / "overlay_tools/cli"
        package.mkdir(parents=True)
        marker = location / "module-executed"
        markers.append(marker)
        (package.parent / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "overlay_info.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\nprint('checkout script')\n"
        )
        startup_marker = location / "startup-executed"
        markers.append(startup_marker)
        (location / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(startup_marker)!r}).touch()\n"
        )
    tools = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(tools / "bin/overlay-info"), "--overlay-path", str(root), "--json"],
        cwd=root if from_overlay else tmp_path,
        env={**os.environ, "PYTHONPATH": str(injected), "UV_OFFLINE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not any(marker.exists() for marker in markers)
    assert json.loads(result.stdout)["checkout"]["root"] == str(root)


@pytest.mark.parametrize("from_overlay", [False, True])
def test_wrapper_uses_its_own_source_without_bytecode_or_startup_writes(tmp_path, from_overlay):
    if shutil.which("uv") is None:
        pytest.skip("Executable wrapper requires uv")
    tools = Path(__file__).resolve().parents[1]
    selected = tmp_path / "selected-tools"
    shutil.copytree(
        tools / "src", selected / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (selected / "bin").mkdir()
    shutil.copy2(tools / "bin/overlay-info", selected / "bin/overlay-info")
    shutil.copy2(tools / "pyproject.toml", selected / "pyproject.toml")
    cli = selected / "src/overlay_tools/cli/overlay_info.py"
    cli.write_text(cli.read_text().replace('"Overlay context"', '"Selected tools source"'))
    root = make_overlay(tmp_path / "overlay")
    marker = root / "startup-executed"
    (root / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    hostile = root / "overlay_tools/cli"
    hostile.mkdir(parents=True)
    (hostile.parent / "__init__.py").write_text("")
    (hostile / "__init__.py").write_text("")
    (hostile / "overlay_info.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nprint('checkout script')\n"
    )
    source_before = {
        path.relative_to(selected): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in selected.rglob("*")
        if path.is_file()
    }

    result = subprocess.run(
        [str(selected / "bin/overlay-info"), "--overlay-path", str(root)],
        cwd=root if from_overlay else tmp_path,
        env={
            **os.environ,
            "UV_PROJECT_ENVIRONMENT": sys.prefix,
            "UV_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(root),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Selected tools source\n")
    assert not marker.exists()
    assert list(selected.rglob("*.pyc")) == []
    assert list(selected.rglob("__pycache__")) == []
    assert not (selected / ".venv").exists()
    assert not (selected / "uv.lock").exists()
    assert source_before == {
        path.relative_to(selected): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in selected.rglob("*")
        if path.is_file()
    }


def test_module_invalid_root_returns_exit_two_and_no_json(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "overlay_tools.cli.overlay_info",
            "--json",
            "--overlay-path",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "valid Gentoo overlay" in result.stderr


def test_policy_refresh_and_masks_are_not_update_exclusions(tmp_path, capsys):
    root = make_overlay(tmp_path)
    (root / "profiles/package.mask").write_text("dev-util/editor\n")
    policy = root / "metadata/update-exclusions.json"
    policy.write_text(json.dumps({"net-im/chat": "Paused.", "gone/old": "Removed."}))

    code, output = invoke(root, capsys, "--json")
    data = json.loads(output.out)

    assert code == 0
    assert data["inventory"]["package_count"] == 2
    assert data["update_policy"]["active_count"] == 1
    assert data["update_policy"]["active_exclusions"]["items"][0]["atom"] == "net-im/chat"
    assert {item["atom"] for item in data["update_policy"]["exclusions"]["items"]} == {
        "net-im/chat",
        "gone/old",
    }
    policy.write_text(json.dumps({"dev-util/editor": "New pause."}))
    code, output = invoke(root, capsys, "--json")
    assert code == 0
    assert json.loads(output.out)["update_policy"]["active_exclusions"]["items"][0]["atom"] == (
        "dev-util/editor"
    )


@pytest.mark.parametrize("flags", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize("newline_kind", ["crlf", "mixed"])
@pytest.mark.parametrize("mask_is_directory", [False, True])
def test_full_configuration_preserves_raw_newlines_in_both_public_formats(
    tmp_path, capsys, flags, newline_kind, mask_is_directory
):
    root = make_overlay(tmp_path)
    if newline_kind == "crlf":
        raw = {
            "profiles/repo_name": b"fixture-overlay\r\n",
            "profiles/eapi": b"8\r\n",
            "metadata/layout.conf": b"masters = gentoo\r\nthin-manifests = true\r\n",
            "profiles/package.mask": "# Deprecated café\r\ndev-util/editor\r\n".encode(),
        }
    else:
        raw = {
            "profiles/repo_name": b"fixture-overlay\r\n\n\r",
            "profiles/eapi": b"8\r\n\n\r",
            "metadata/layout.conf": b"masters = gentoo\r\n# Comment\n\rthin-manifests = true\r",
            "profiles/package.mask": (
                "# Deprecated café\r\ndev-util/editor\n\rnet-im/chat\r".encode()
            ),
        }
    if mask_is_directory:
        (root / "profiles/package.mask").mkdir()
        raw["profiles/package.mask/deprecated"] = raw.pop("profiles/package.mask")
    for relative, content in raw.items():
        (root / relative).write_bytes(content)

    code, output = invoke(root, capsys, *flags, "--full")

    assert code == 0
    assert output.err == ""
    if "--json" in flags:
        actual = json.loads(output.out)["full_configuration"]
    else:
        actual = {}
        for relative in raw:
            prefix = relative.replace("_", " ") + ": "
            line = next(line.strip() for line in output.out.splitlines() if prefix in line)
            actual[relative] = json.loads(line.removeprefix(prefix))
    assert {relative: actual[relative].encode("utf-8") for relative in raw} == raw


@pytest.mark.parametrize("mask_is_directory", [False, True])
def test_masks_are_read_only_with_full_and_unreadable_masks_fail_honestly(
    tmp_path, capsys, mask_is_directory
):
    root = make_overlay(tmp_path)
    path = root / "profiles/package.mask"
    if mask_is_directory:
        path.mkdir()
        path = path / "deprecated"
    path.write_bytes(b"\xff")

    code, output = invoke(root, capsys, "--json")
    assert code == 0
    assert "full_configuration" not in json.loads(output.out)
    code, output = invoke(root, capsys, "--json", "--full")
    assert code == 2
    assert output.out == ""
    assert "profiles/package.mask" in output.err
    path.write_text("# Deprecated desktop\ndev-util/editor\n")

    code, output = invoke(root, capsys, "--json", "--full")

    assert code == 0
    data = json.loads(output.out)
    assert data["full_configuration"][str(path.relative_to(root))] == (
        "# Deprecated desktop\ndev-util/editor\n"
    )


def test_git_partial_checkout_does_not_lazy_fetch_missing_objects(tmp_path, capsys):
    root = make_overlay(tmp_path)
    initialize_git(root)
    tree = git(root, "rev-parse", "HEAD^{tree}")
    (root / ".git/objects" / tree[:2] / tree[2:]).unlink()
    marker = tmp_path / "fetch-was-run"
    transport = tmp_path / "fetch-transport"
    transport.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    transport.chmod(0o755)
    git(root, "config", "core.repositoryformatversion", "1")
    git(root, "config", "extensions.partialClone", "origin")
    git(root, "config", "remote.origin.promisor", "true")
    git(root, "config", "remote.origin.url", f"ext::{transport}")
    git(root, "config", "protocol.ext.allow", "always")

    code, output = invoke(root, capsys, "--json")

    assert code == 0
    report = json.loads(output.out)["checkout"]["git"]
    assert report["status"] == "unchecked"
    assert report["revision"] is None
    assert report["dirty"] is None
    assert not marker.exists()
