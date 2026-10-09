"""Assertions at the public Docker runner seam, without launching staged payloads."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from overlay_tools.cli import test_ebuild

EBUILD = "dev-util/example/example-1.ebuild"
RUNNER = test_ebuild.TOOLS_ROOT / "docker/run-ebuild"


@pytest.fixture
def runner(tmp_path: Path):
    """Fake Portage only; invoke the actual runner with its normal argv/environment."""
    repo = tmp_path / "overlay"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles/repo_name").write_text("fixture-overlay\n")
    (repo / EBUILD).parent.mkdir(parents=True)
    (repo / EBUILD).write_text("EAPI=8\n")
    config = tmp_path / "config/etc/portage"
    config.mkdir(parents=True)
    (config / "make.profile").touch()
    binary = tmp_path / "bin"
    binary.mkdir()
    events = tmp_path / "events"
    fixture = tmp_path / "fixtures.json"
    programs = {
        "portageq": '#!/bin/sh\nprintf "portageq\\n" >> "$EVENTS"\n'
        'case "$1" in\nget_repo_path) printf "%s\\n" "$OVERLAY_REPO" ;;\n'
        'envvar) printf "%s\\n" "$FAKE_TMPDIR" ;;\nesac\n',
        "emerge": '#!/bin/sh\nprintf "emerge\\n" >> "$EVENTS"\n',
        "ebuild": """#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
with open(os.environ['EVENTS'], 'a') as log:
    log.write('ebuild\\n')
ebuild = Path(sys.argv[1])
build = Path(os.environ['PORTAGE_TMPDIR']) / 'portage'
image = build / ebuild.parents[1].name / ebuild.stem / 'image'
shutil.rmtree(image, ignore_errors=True)
if os.environ.get('IMAGE_TARGET'):
    image.parent.mkdir(parents=True, exist_ok=True)
    image.symlink_to(os.environ['IMAGE_TARGET'])
    sys.exit(0)
image.mkdir(parents=True, exist_ok=True)
for item in json.loads(Path(os.environ['FIXTURES']).read_text()):
    path = image / item['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    if 'target' in item:
        path.symlink_to(item['target'])
    elif item.get('type') == 'directory':
        path.mkdir(exist_ok=True)
    elif item.get('type') == 'fifo':
        os.mkfifo(path)
    else:
        path.write_text('#!/bin/sh\\ntouch "' + os.environ['PAYLOAD_MARKER'] + '"\\n')
    if 'mode' in item:
        path.chmod(int(item['mode'], 8))
""",
    }
    for name, body in programs.items():
        path = binary / name
        path.write_text(body)
        path.chmod(0o755)

    def invoke(
        *args: str,
        files: list[dict[str, str]] | None = None,
        ebuild: str = EBUILD,
        image_target: str = "",
    ):
        fixture.write_text(json.dumps(files or []))
        (repo / ebuild).parent.mkdir(parents=True, exist_ok=True)
        (repo / ebuild).write_text("EAPI=8\n")
        env = {
            **os.environ,
            "PATH": f"{binary}:/usr/bin:/bin",
            "OVERLAY_REPO": str(repo),
            "PORTAGE_CONFIGROOT": str(tmp_path / "config"),
            "FAKE_TMPDIR": str(tmp_path / "build"),
            "FIXTURES": str(fixture),
            "EVENTS": str(events),
            "PAYLOAD_MARKER": str(tmp_path / "payload-executed"),
            "IMAGE_TARGET": image_target,
        }
        return subprocess.run(
            ["bash", str(RUNNER), ebuild, *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return invoke


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([{"path": "usr/bin/tool", "mode": "0755"}], 0),
        ([{"path": "usr/bin/tool", "mode": "0644"}], 1),
        ([{"path": "usr/bin/tool", "type": "directory", "mode": "0755"}], 1),
        (
            [
                {"path": "opt/tool", "mode": "0711"},
                {"path": "usr/bin/tool", "target": "/opt/tool"},
            ],
            0,
        ),
    ],
)
def test_executable_requires_regular_file_with_execute_bits_without_running_it(
    runner, tmp_path: Path, files: list[dict[str, str]], code: int
) -> None:
    result = runner("--expect-executable", "usr/bin/tool", files=files)
    assert result.returncode == code, result.stderr
    if code:
        assert "Assertion failed" in result.stderr
    assert not (tmp_path / "payload-executed").exists()
    assert "ebuild" in (tmp_path / "events").read_text()


@pytest.mark.parametrize(
    ("spec", "files", "code"),
    [
        ("file:opt/tool", [{"path": "opt/tool"}], 0),
        ("directory:opt/tool", [{"path": "opt/tool", "type": "directory"}], 0),
        ("symlink:opt/tool", [{"path": "opt/tool", "target": "/missing"}], 0),
        ("file:opt/tool", [{"path": "opt/tool", "target": "/missing"}], 1),
        ("directory:opt/tool", [{"path": "opt/tool"}], 1),
        ("file:opt/tool", [{"path": "opt/tool", "type": "fifo"}], 1),
    ],
)
def test_type_distinguishes_final_symlink_and_special_files(runner, spec, files, code):
    result = runner("--expect-type", spec, files=files)
    assert result.returncode == code, result.stderr
    if code:
        assert "Assertion failed" in result.stderr


@pytest.mark.parametrize(
    ("expected", "actual", "code"),
    [
        ("0755", "0755", 0),
        ("755", "0755", 0),
        ("4711", "4711", 0),
        ("0755", "0644", 1),
        ("0711", "4711", 1),
        ("0000", "0000", 0),
    ],
)
def test_mode_compares_all_permission_and_special_bits(runner, expected, actual, code):
    result = runner(
        "--expect-mode",
        f"{expected}:usr/bin/tool",
        files=[
            {"path": "opt/tool", "mode": actual},
            {"path": "usr/bin/tool", "target": "/opt/tool"},
        ],
    )
    assert result.returncode == code, result.stderr
    if code:
        assert "Assertion failed" in result.stderr


@pytest.mark.parametrize("target", ["/missing", "../../opt/tool", "$(touch nope);a=b c"])
def test_link_target_is_exact_text_without_resolution_or_shell_execution(runner, target):
    files = [{"path": "usr/bin/tool", "target": target}]
    result = runner("--expect-link-target", f"usr/bin/tool={target}", files=files)
    assert result.returncode == 0, result.stderr
    result = runner("--expect-link-target", "usr/bin/tool=other", files=files)
    assert result.returncode == 1
    assert "Assertion failed" in result.stderr


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([{"path": "usr/bin/tool", "target": "/opt/tool"}, {"path": "opt/tool"}], 0),
        (
            [
                {"path": "usr/bin/tool", "target": "../../opt/tool"},
                {"path": "opt/tool", "target": "../srv/real"},
                {"path": "srv/real"},
            ],
            0,
        ),
        ([{"path": "usr/bin/tool", "target": "/missing"}], 1),
        ([{"path": "usr/bin/tool", "target": "/usr/bin/tool"}], 1),
        ([{"path": "usr/bin/tool"}], 1),
    ],
)
def test_resolved_link_requires_a_link_and_existing_image_rooted_target(runner, files, code):
    result = runner("--expect-resolved-link", "usr/bin/tool", files=files)
    assert result.returncode == code, result.stderr
    if code:
        assert "Assertion failed" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--expect-type", "socket:usr/bin/tool"],
        ["--expect-mode", "0788:usr/bin/tool"],
        ["--expect-mode", "07555:usr/bin/tool"],
        ["--expect-link-target", "usr/bin/tool="],
        ["--expect-executable", "../../outside"],
        ["--expect-resolved-link"],
        ["--assertions-json", '{"kind":"executable","path":"usr/bin/tool"}'],
        ["--assertions-json", '[{"kind":"command","path":"usr/bin/tool"}]'],
        ["--assertions-json", '[{"kind":"mode","path":"usr/bin/tool","mode":755}]'],
        ["--assertions-json", '[{"kind":"exists","path":"../outside"}]'],
    ],
)
def test_malformed_specs_fail_before_portage_or_config_writes(runner, tmp_path, args):
    result = runner(*args)
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / "events").exists()
    assert not (tmp_path / "config/etc/portage/repos.conf").exists()


def test_host_cli_transports_validated_assertions_as_json_with_readonly_helper(
    runner, tmp_path, monkeypatch
):
    calls = []

    def docker(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(test_ebuild, "require_tool", lambda *a: Path("/usr/bin/docker"))
    monkeypatch.setattr(test_ebuild, "run", docker)
    assert (
        test_ebuild.main(
            [
                EBUILD,
                "--overlay-path",
                str(tmp_path / "overlay"),
                "--expect",
                "usr/bin/tool",
                "--expect-executable",
                "usr/bin/tool",
                "--expect-type",
                "file:usr/bin/tool",
                "--expect-mode",
                "4711:usr/bin/tool",
                "--expect-link-target",
                "usr/bin/link=/usr/bin/tool",
                "--expect-resolved-link",
                "usr/bin/link",
            ]
        )
        == 0
    )
    cmd = calls[-1]
    helper_mount = next(arg for arg in cmd if "dst=/usr/local/bin/staged-assertions.py" in arg)
    assert helper_mount.endswith(",readonly")
    specs = cmd[cmd.index(EBUILD) + 1 :]
    assert specs[0] == "--assertions-json"
    checks = json.loads(specs[1])
    assert checks == [
        {"kind": "exists", "path": "usr/bin/tool"},
        {"kind": "executable", "path": "usr/bin/tool"},
        {"kind": "type", "path": "usr/bin/tool", "type": "file"},
        {"kind": "mode", "path": "usr/bin/tool", "mode": "4711"},
        {"kind": "link-target", "path": "usr/bin/link", "target": "/usr/bin/tool"},
        {"kind": "resolved-link", "path": "usr/bin/link"},
    ]
    result = runner(
        *specs,
        files=[
            {"path": "usr/bin/tool", "mode": "4711"},
            {"path": "usr/bin/link", "target": "/usr/bin/tool"},
        ],
    )
    assert result.returncode == 0, result.stderr


def test_symlinked_install_image_is_an_assertion_failure(runner, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tool").touch()
    (outside / "tool").chmod(0o755)
    result = runner("--expect-executable", "tool", image_target=str(outside))
    assert result.returncode == 1, result.stderr
    assert "install image must be a real directory" in result.stderr


@pytest.mark.parametrize("flag", ["--expect-executable", "--expect-resolved-link", "--expect-mode"])
def test_absolute_link_never_reads_host_target(runner, tmp_path, flag):
    outside = tmp_path / "outside"
    outside.write_text("host only")
    outside.chmod(0o755)
    spec = "0755:usr/bin/tool" if flag == "--expect-mode" else "usr/bin/tool"
    result = runner(flag, spec, files=[{"path": "usr/bin/tool", "target": str(outside)}])
    assert result.returncode == 1, result.stderr
    assert "Assertion failed" in result.stderr


@pytest.mark.parametrize("target", ["../../../outside", "/../outside", "loop", "other"])
def test_rejects_escape_and_link_cycles(runner, target):
    files = [
        {"path": "usr/bin/loop", "target": target},
        {"path": "usr/bin/other", "target": "loop"},
    ]
    result = runner("--expect-resolved-link", "usr/bin/loop", files=files)
    assert result.returncode == 1
    assert "Assertion failed" in result.stderr


@pytest.mark.parametrize("count", [40, 41])
def test_link_chain_has_a_bounded_depth(runner, count):
    files = [{"path": f"opt/link{i}", "target": f"link{i + 1}"} for i in range(count)]
    files.append({"path": f"opt/link{count}", "mode": "0755"})
    result = runner("--expect-executable", "opt/link0", files=files)
    assert result.returncode == (0 if count == 40 else 1), result.stderr


def test_strong_checks_follow_parent_links_inside_image_but_legacy_does_not(runner):
    files = [{"path": "opt/real/tool", "mode": "0755"}, {"path": "usr/bin", "target": "/opt/real"}]
    result = runner(
        "--expect-executable", "usr/bin/tool", "--expect-type", "file:usr/bin/tool", files=files
    )
    assert result.returncode == 0, result.stderr
    result = runner("usr/bin/tool", files=files)
    assert result.returncode == 1
    assert "Missing staged path" in result.stderr


def test_legacy_expect_preserves_path_names_that_look_like_new_options(
    runner, tmp_path, monkeypatch
):
    calls = []

    def docker(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(test_ebuild, "require_tool", lambda *a: Path("/usr/bin/docker"))
    monkeypatch.setattr(test_ebuild, "run", docker)
    assert (
        test_ebuild.main(
            [
                EBUILD,
                "--overlay-path",
                str(tmp_path / "overlay"),
                "--expect=--expect-mode",
            ]
        )
        == 0
    )
    cmd = calls[-1]
    result = runner(
        *cmd[cmd.index(EBUILD) + 1 :], files=[{"path": "--expect-mode", "target": "/missing"}]
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("revision", ["1", "1-r1"])
def test_openrc_registry_checks_adapter_artifacts_without_starting_service(
    runner, tmp_path, revision
):
    metadata = tmp_path / "overlay/metadata"
    metadata.mkdir()
    source = test_ebuild.TOOLS_ROOT.parents[2] / "metadata/test-assertions.json"
    (metadata / "test-assertions.json").write_text(source.read_text())
    ebuild = f"dev-util/t3code-openrc/t3code-openrc-{revision}.ebuild"
    files = [
        {"path": "etc/init.d/t3code", "mode": "0755"},
        {"path": "etc/conf.d/t3code", "mode": "0644"},
        {"path": "usr/libexec/t3code-openrc", "mode": "0755"},
        {"path": f"usr/share/doc/t3code-openrc-{revision}", "type": "directory"},
    ]
    result = runner("--package-checks", ebuild=ebuild, files=files)
    assert result.returncode == 0, result.stderr
    assert "OK: executable etc/init.d/t3code" in result.stdout
    assert "OK: mode usr/libexec/t3code-openrc" in result.stdout
    files[2]["mode"] = "0644"
    result = runner("--package-checks", ebuild=ebuild, files=files)
    assert result.returncode == 1
    assert "usr/libexec/t3code-openrc" in result.stderr
    assert not (tmp_path / "payload-executed").exists()


@pytest.mark.parametrize("strong", [False, True])
def test_legacy_presence_accepts_dangling_final_link_even_with_strong_checks(runner, strong):
    args = ["usr/bin/tool"]
    if strong:
        args.extend(["--expect-type", "symlink:usr/bin/tool"])
    result = runner(*args, files=[{"path": "usr/bin/tool", "target": "/missing"}])
    assert result.returncode == 0, result.stderr


def test_package_checks_are_opt_in_and_combine_with_explicit_assertions(runner, tmp_path):
    metadata = tmp_path / "overlay/metadata"
    metadata.mkdir()
    (metadata / "test-assertions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "packages": {
                    "=dev-util/example-1": [{"kind": "executable", "path": "usr/bin/tool"}]
                },
            }
        )
    )
    files = [{"path": "usr/bin/tool", "mode": "0644"}]
    assert runner(files=files).returncode == 0
    result = runner("--package-checks", files=files)
    assert result.returncode == 1
    assert "Assertion failed" in result.stderr
    result = runner(
        "--package-checks",
        "--expect-type",
        "file:usr/bin/tool",
        files=[{"path": "usr/bin/tool", "mode": "0755"}],
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{",
        '{"version":1,"packages":{}}',
        '{"version":2,"packages":{}}',
        '{"version":true,"packages":{}}',
        '{"version":1,"packages":{},"command":"true"}',
        '{"version":1,"packages":{"dev-util/example":[{"kind":"exists","path":"x"}]}}',
        '{"version":1,"packages":{"=dev-util/example-1":[{"kind":"command","path":"x"}]}}',
        '{"version":1,"packages":{"=dev-util/example-1":[]}}',
        '{"version":1,"version":1,"packages":{}}',
        (
            '{"version":1,"packages":{"=dev-util/example-1":[{"kind":"exists","path":"x"}],'
            '"=dev-util/.bad-1":[{"kind":"exists","path":"x"}]}}'
        ),
        (
            '{"version":1,"packages":{"=dev-util/example-1":[{"kind":"exists","path":"x"}],'
            '"=dev-util/bad.name-1":[{"kind":"exists","path":"x"}]}}'
        ),
        '{"version":1,"packages":{"=dev-util/example-2":[{"kind":"exists","path":"x"}]}}',
        (
            '{"version":1,"packages":{"=dev-util/example-1":[{"kind":"exists","path":"x",'
            '"command":"true"}]}}'
        ),
    ],
)
def test_bad_or_missing_package_registry_fails_before_side_effects(runner, tmp_path, content):
    if content is not None:
        metadata = tmp_path / "overlay/metadata"
        metadata.mkdir()
        (metadata / "test-assertions.json").write_text(content)
    result = runner("--package-checks")
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / "events").exists()
    assert not (tmp_path / "config/etc/portage/repos.conf").exists()
    assert "package checks registry" in result.stderr


@pytest.mark.parametrize(
    "atom",
    [
        "=+dev-util/bad-1",
        "=dev-util/+bad-1",
        "=dev-util/bad-1-2",
        "=dev-util/bad-1-2-r1",
        "=dev-util/bad-1a-2",
        "=dev-util/bad-1_alpha-2",
        "=dev-util/bad-1_beta2-2",
        "=dev-util/bad-1_pre-2",
        "=dev-util/bad-1_rc3-2",
        "=dev-util/bad-1_p-2",
        "=dev-util/bad-1_alpha2_beta3_pre4_rc5_p6-2",
        "=dev-util/bad-1-r2-3",
        "=dev-util/bad-1-r2-3-r4",
        "=dev-util/bad-1-r1-r2",
        "=dev-util/bad-1-r",
        "=dev-util/bad-1.2-r1-r2",
        "=dev-util/bad-1*",
        "=dev-util/bad-1:0",
        "=dev-util/bad-1::repo",
        "=dev-util/bad-1[use]",
        " =dev-util/bad-1",
        "=dev-util/bad-1 ",
        ">=dev-util/bad-1",
        "=dev-util/bad-1\n",
    ],
)
def test_invalid_unrelated_registry_atom_fails_before_any_portage_side_effect(
    runner, tmp_path, atom
):
    metadata = tmp_path / "overlay/metadata"
    metadata.mkdir()
    checks = [{"kind": "exists", "path": "x"}]
    (metadata / "test-assertions.json").write_text(
        json.dumps({"version": 1, "packages": {"=dev-util/example-1": checks, atom: checks}})
    )
    config = tmp_path / "config"

    def config_state():
        return {
            str(path.relative_to(config)): (
                path.stat().st_mode,
                path.read_bytes() if path.is_file() else None,
            )
            for path in config.rglob("*")
        }

    before = config_state()
    result = runner("--package-checks", files=[{"path": "x"}])
    assert result.returncode == 2, result.stderr
    assert "invalid exact package atom" in result.stderr
    assert atom in result.stderr
    assert not (tmp_path / "events").exists()
    assert config_state() == before
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "payload-executed").exists()


@pytest.mark.parametrize(
    "atom",
    [
        "=dev+util/good+name-1",
        "=_dev.util/_good-1",
        "=dev-util/good--1",
        "=dev-util/good-1foo-2",
        "=dev-util/good-1_pretty-2",
        "=dev-util/good-1-r-2",
        "=dev-util/good-1-r1-name-2",
        "=dev-util/good-1.2a_alpha_beta2_pre_rc3_p4-r5",
        "=dev-util/good-01.002-r00",
    ],
)
def test_valid_unrelated_registry_atoms_preserve_pms_names_and_versions(runner, tmp_path, atom):
    metadata = tmp_path / "overlay/metadata"
    metadata.mkdir()
    checks = [{"kind": "exists", "path": "x"}]
    (metadata / "test-assertions.json").write_text(
        json.dumps({"version": 1, "packages": {"=dev-util/example-1": checks, atom: checks}})
    )
    result = runner("--package-checks", files=[{"path": "x"}])
    assert result.returncode == 0, result.stderr
    assert "OK: exists x" in result.stdout
    assert "ebuild" in (tmp_path / "events").read_text()
    assert not (tmp_path / "payload-executed").exists()


@pytest.mark.parametrize(
    ("flag", "first", "second"),
    [
        ("--expect-executable", "tool", "missing"),
        ("--expect-type", "file:tool", "file:missing"),
        ("--expect-mode", "0755:tool", "0755:missing"),
        ("--expect-link-target", "alias=/tool", "missing=/tool"),
        ("--expect-resolved-link", "alias", "missing"),
    ],
)
def test_every_strong_cli_option_is_repeatable_and_checks_second_value(
    runner, tmp_path, monkeypatch, flag, first, second
):
    calls = []

    def docker(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(test_ebuild, "require_tool", lambda *a: Path("/usr/bin/docker"))
    monkeypatch.setattr(test_ebuild, "run", docker)
    assert (
        test_ebuild.main(
            [EBUILD, "--overlay-path", str(tmp_path / "overlay"), flag, first, flag, second]
        )
        == 0
    )
    cmd = calls[-1]
    result = runner(
        *cmd[cmd.index(EBUILD) + 1 :],
        files=[
            {"path": "tool", "mode": "0755"},
            {"path": "alias", "target": "/tool"},
        ],
    )
    assert result.returncode == 1
    assert "Assertion failed" in result.stderr and "missing" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--expect-executable", "/tool"],
        ["--expect-type", "socket:tool"],
        ["--expect-mode", "7558:tool"],
        ["--expect-link-target", "tool="],
        ["--expect-resolved-link", "usr/../tool"],
        ["--expect-executable", "tool", "--expect-mode", "0759:tool"],
    ],
)
def test_host_rejects_all_malformed_specs_before_docker(runner, tmp_path, monkeypatch, args):
    calls = []
    monkeypatch.setattr(test_ebuild, "require_tool", lambda *a: calls.append(a))
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main([EBUILD, "--overlay-path", str(tmp_path / "overlay"), "--build", *args])
    assert exc.value.code == 2
    assert calls == []


def test_shell_characters_in_link_text_never_execute(runner, tmp_path):
    marker = tmp_path / "shell-evaluated"
    target = f"$(touch {marker});a=b c"
    result = runner(
        "--expect-link-target", f"alias={target}", files=[{"path": "alias", "target": target}]
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("use_registry", [False, True])
def test_host_package_checks_validate_before_docker(runner, tmp_path, monkeypatch, use_registry):
    calls = []
    monkeypatch.setattr(test_ebuild, "require_tool", lambda *args: calls.append(args))
    if use_registry:
        metadata = tmp_path / "overlay/metadata"
        metadata.mkdir()
        (metadata / "test-assertions.json").write_text('{"version":1,"packages":{}}')
    with pytest.raises(SystemExit) as exc:
        test_ebuild.main([EBUILD, "--overlay-path", str(tmp_path / "overlay"), "--package-checks"])
    assert exc.value.code == 2
    assert calls == []


def test_host_package_checks_are_expanded_and_combined(runner, tmp_path, monkeypatch):
    metadata = tmp_path / "overlay/metadata"
    metadata.mkdir()
    (metadata / "test-assertions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "packages": {
                    "=dev-util/example-1": [{"kind": "mode", "path": "tool", "mode": "4711"}]
                },
            }
        )
    )
    calls = []

    def docker(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(test_ebuild, "require_tool", lambda *a: Path("/usr/bin/docker"))
    monkeypatch.setattr(test_ebuild, "run", docker)
    assert (
        test_ebuild.main(
            [
                EBUILD,
                "--overlay-path",
                str(tmp_path / "overlay"),
                "--package-checks",
                "--expect-executable",
                "tool",
            ]
        )
        == 0
    )
    cmd = calls[-1]
    specs = cmd[cmd.index(EBUILD) + 1 :]
    assert json.loads(specs[1]) == [
        {"kind": "executable", "path": "tool"},
        {"kind": "mode", "path": "tool", "mode": "4711"},
    ]
    result = runner(*specs, files=[{"path": "tool", "mode": "4711"}])
    assert result.returncode == 0, result.stderr
