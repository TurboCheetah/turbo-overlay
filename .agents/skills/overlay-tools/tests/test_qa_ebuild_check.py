"""Controller contracts through the public executable and CI shell body."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_qa_ebuild_cli import (
    assert_launcher_import_isolation,
    external_tool_spies,
    git,
    make_overlay,
    option_prefixes,
    public_launcher,
    snapshot,
)

TOOLS = Path(__file__).resolve().parents[1]
LAUNCHER = TOOLS / "bin/qa-ebuild-check"


def invoke(root: Path, *args: str, env=None):
    return subprocess.run(
        [str(LAUNCHER), "--overlay-path", str(root), *args],
        cwd=root.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("source", ["cwd", "pythonpath", "sitecustomize", "other-editable"])
def test_controller_imports_own_source_in_isolated_python(tmp_path, source):
    assert_launcher_import_isolation(tmp_path, "qa-ebuild-check", source)


@pytest.mark.parametrize(
    "prefix",
    option_prefixes(
        ["--informational", "--initial", "--overlay-path", "--changed-since", "--help"]
    ),
)
def test_controller_rejects_abbreviated_options_without_info_override(tmp_path, prefix):
    root = make_overlay(tmp_path / "repo", thin=True)
    (root / "dev-util/adapter/metadata.xml").write_text("<broken>")
    environment, log = external_tool_spies(tmp_path)
    if "--informational".startswith(prefix):
        arguments = [prefix, "dev-util/adapter/metadata.xml"]
    elif "--initial".startswith(prefix):
        arguments = [prefix]
    else:
        arguments = ["--initial", prefix]
        if "--overlay-path".startswith(prefix):
            arguments.append(str(root))
        elif "--changed-since".startswith(prefix):
            arguments.append("HEAD")
    before = snapshot(tmp_path)
    result = invoke(root, *arguments, env=environment)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "INCONCLUSIVE: examined 0 package(s)" in result.stdout
    assert "input_environment" in result.stdout
    assert "Invalid XML" not in result.stdout
    assert "wrapper exit" not in result.stdout + result.stderr
    assert "informational pre-commit QA" not in result.stdout
    assert "not applicable" not in result.stdout
    assert not log.exists()
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["--initial", "--", "--informational"],
        ["--changed-since", "HEAD", "--", "--informational"],
        ["--", "--informational"],
        ["--initial", "--bad", "--", "--informational"],
        ["--initial", "--overlay-path", "--informational"],
        ["--changed-since", "--informational"],
        ["--overlay-path", "--", "--informational"],
        ["--initial", "--overlay-path=--informational"],
        ["--changed-since=--informational"],
        ["--initial", "--informational=true"],
    ],
)
@pytest.mark.parametrize("provisioned", [True, False], ids=["prepared", "missing-venv"])
def test_controller_informational_values_cannot_override_ci_failure(
    tmp_path, arguments, provisioned
):
    root = make_overlay(tmp_path / "repo", thin=True)
    if provisioned:
        result = invoke(root, *arguments)
    else:
        launcher = tmp_path / "tools/bin/qa-ebuild-check"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(LAUNCHER.read_bytes())
        launcher.chmod(0o755)
        result = subprocess.run(
            [str(launcher), "--overlay-path", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        assert not (tmp_path / "tools/.venv").exists()
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "INCONCLUSIVE" in output
    if provisioned:
        assert "INCONCLUSIVE: examined 0 package(s)" in output
        assert "input_environment" in output
    else:
        assert "overlay-tools .venv is missing" in output
    assert "wrapper exit" not in output
    assert "informational pre-commit QA" not in output
    assert "PASS" not in output


@pytest.mark.parametrize("provisioned", [True, False], ids=["prepared", "missing-venv"])
@pytest.mark.parametrize(
    "arguments",
    [
        ["--informational", "--", "--informational"],
        ["--informational", "--bad", "--", "--informational"],
        ["--informational", "--overlay-path", "--informational"],
        ["--overlay-path", "--informational", "--informational"],
        ["--changed-since", "--informational", "--informational"],
        ["--overlay-path=--informational", "--informational"],
        ["--changed-since=--informational", "--informational"],
        ["--informational", "--initial", "--", "--informational"],
    ],
)
def test_controller_real_informational_flag_keeps_honest_zero_exit(
    tmp_path, provisioned, arguments
):
    root = make_overlay(tmp_path / "repo", thin=True)
    if provisioned:
        result = invoke(root, *arguments)
    else:
        launcher = tmp_path / "tools/bin/qa-ebuild-check"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(LAUNCHER.read_bytes())
        launcher.chmod(0o755)
        result = subprocess.run(
            [str(launcher), "--overlay-path", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        assert not (tmp_path / "tools/.venv").exists()
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "INCONCLUSIVE" in output
    assert "authoritative QA exit: 2" in output
    assert "informational wrapper exit: 0" in output
    assert "PASS:" not in output
    if not provisioned:
        assert "overlay-tools .venv is missing" in output
        assert "not package PASS" in output


def test_hook_reports_findings_without_blocking_or_mutating(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    (root / "dev-util/adapter/metadata.xml").write_text("<broken>")
    make_overlay(root, thin=True, atom="dev-util/other")
    before = snapshot(root)
    result = invoke(root, "--informational", "dev-util/adapter/metadata.xml")
    assert result.returncode == 0, result.stderr
    assert "informational" in result.stdout
    assert "FAIL: examined 1 package(s)" in result.stdout
    assert "Invalid XML" in result.stdout
    assert "authoritative QA exit: 1" in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "path",
    [
        "dev-util/adapter/adapter-1.ebuild",
        "dev-util/adapter/Manifest",
        "dev-util/adapter/metadata.xml",
        "dev-util/adapter/files/adapter.patch",
        "metadata/md5-cache/dev-util/adapter-1",
    ],
)
def test_hook_selects_exact_package_inputs_once(tmp_path, path):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    result = invoke(root, "--informational", path, path)
    assert result.returncode == 0
    assert "PASS: examined 1 package(s)" in result.stdout


@pytest.mark.parametrize(
    "path", ["metadata/layout.conf", "profiles/package.mask", "eclass/a.eclass"]
)
def test_hook_shared_inputs_select_all_packages(tmp_path, path):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    result = invoke(root, "--informational", path)
    assert result.returncode == 0
    assert "PASS: examined 2 package(s)" in result.stdout


def test_hook_deleted_cache_reports_real_failure(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    (root / "metadata/md5-cache/dev-util/adapter-1").unlink()
    result = invoke(root, "--informational", "metadata/md5-cache/dev-util/adapter-1")
    assert result.returncode == 0
    assert "FAIL: examined 1 package(s)" in result.stdout
    assert "Missing metadata cache" in result.stdout


@pytest.mark.parametrize("paths", [[], ["dev-util/adapter/README.md"], ["../outside"], ["--bad"]])
def test_hook_empty_unrelated_or_invalid_inputs_never_report_package_pass(tmp_path, paths):
    root = make_overlay(tmp_path / "repo", thin=True)
    result = invoke(root, "--informational", *paths)
    assert result.returncode == 0
    assert "INCONCLUSIVE: examined 0 package(s)" in result.stdout
    assert "authoritative QA exit: 2" in result.stdout
    assert "PASS:" not in result.stdout


@pytest.mark.parametrize("informational, expected", [(True, 0), (False, 2)])
def test_missing_adapter_environment_is_not_a_pass(tmp_path, informational, expected):
    launcher = tmp_path / "tools/bin/qa-ebuild-check"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(LAUNCHER.read_bytes())
    launcher.chmod(0o755)
    args = [str(launcher), "--overlay-path", str(tmp_path)]
    if informational:
        args.append("--informational")
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == expected
    assert "INCONCLUSIVE" in result.stderr
    assert not (tmp_path / "tools/.venv").exists()


def initialize(root):
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base")
    return git(root, "rev-parse", "HEAD")


def test_ci_tooling_only_is_explicitly_not_applicable(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    base = initialize(root)
    (root / "README.md").write_text("tooling only\n")
    git(root, "add", "README.md")
    result = invoke(root, "--changed-since", base)
    assert result.returncode == 0, result.stderr
    assert "not applicable: no package targets changed" in result.stdout
    assert "PASS:" not in result.stdout


@pytest.mark.parametrize("damage, expected", [(False, 0), (True, 1)])
def test_ci_checks_only_changed_package_and_keeps_authoritative_exit(tmp_path, damage, expected):
    root = make_overlay(tmp_path / "repo", thin=True)
    make_overlay(root, thin=True, atom="dev-util/other")
    (root / "dev-util/other/metadata.xml").write_text("<broken>")
    base = initialize(root)
    (root / "dev-util/adapter/metadata.xml").write_text("<broken>" if damage else "<pkgmetadata/>")
    before = snapshot(root)
    result = invoke(root, "--changed-since", base)
    assert result.returncode == expected, result.stderr
    assert f"{'FAIL' if damage else 'PASS'}: examined 1 package(s)" in result.stdout
    assert "dev-util/other" not in result.stdout
    assert snapshot(root) == before


@pytest.mark.parametrize("base", ["missing-base", "--bad", "$(touch INJECTED)"])
def test_ci_bad_base_is_inconclusive_not_not_applicable(tmp_path, base):
    root = make_overlay(tmp_path / "repo", thin=True)
    initialize(root)
    result = invoke(root, "--changed-since", base)
    assert result.returncode == 2
    assert "INCONCLUSIVE" in result.stdout
    assert "not applicable" not in result.stdout
    assert not (root / "INJECTED").exists()


def ci_step():
    workflow = yaml.safe_load((TOOLS.parents[2] / ".github/workflows/ci.yml").read_text())
    return next(
        step
        for step in workflow["jobs"]["overlay-tools"]["steps"]
        if step["name"] == "Read-only QA for changed packages"
    )


@pytest.mark.parametrize(
    "event, change, expected, marker",
    [
        ("pull_request", "tooling", 0, "not applicable: no package targets changed"),
        ("pull_request", "package", 0, "PASS: examined 1 package(s)"),
        ("push", "package", 0, "PASS: examined 1 package(s)"),
        ("push", "findings", 1, "FAIL: examined 1 package(s)"),
        ("push", "initial", 0, "PASS: examined 1 package(s)"),
        ("push", "initial-findings", 1, "FAIL: examined 1 package(s)"),
        ("pull_request", "missing", 2, "Missing or invalid QA base SHA"),
        ("push", "missing", 2, "Missing or invalid QA base SHA"),
        ("push", "unknown", 2, "INCONCLUSIVE"),
        ("pull_request", "injection", 2, "Missing or invalid QA base SHA"),
        ("workflow_dispatch", "package", 2, "Unsupported QA event"),
    ],
)
def test_workflow_shell_replay(tmp_path, event, change, expected, marker):
    root = make_overlay(tmp_path / "repo", thin=True)
    base = initialize(root)
    if change in {"findings", "initial-findings"}:
        (root / "dev-util/adapter/metadata.xml").write_text("<broken>")
    elif change == "package":
        (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    elif change == "tooling":
        (root / "README.md").write_text("tooling\n")
        git(root, "add", "README.md")
    if change.startswith("initial"):
        base = "0" * 40
    elif change == "missing":
        base = ""
    elif change == "unknown":
        base = "f" * 40
    elif change == "injection":
        base = "$(touch INJECTED)"
    source = root / ".agents/skills/overlay-tools"
    source.parent.mkdir(parents=True)
    source.symlink_to(TOOLS, target_is_directory=True)
    result = subprocess.run(
        ["bash", "-c", ci_step()["run"]],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "QA_EVENT_NAME": event, "QA_PR_BASE": base, "QA_PUSH_BEFORE": base},
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert marker in result.stdout + result.stderr
    assert not (root / "INJECTED").exists()


@pytest.mark.parametrize("missing", ["launcher", "venv", "git", "bash"])
def test_workflow_never_hides_missing_tools(tmp_path, missing):
    root = make_overlay(tmp_path / "repo", thin=True)
    base = initialize(root)
    (root / "dev-util/adapter/metadata.xml").write_text("<pkgmetadata/>")
    tools = root / ".agents/skills/overlay-tools"
    (tools / "bin").mkdir(parents=True)
    launcher = tools / "bin/qa-ebuild-check"
    if missing != "launcher":
        launcher.write_bytes(LAUNCHER.read_bytes())
        launcher.chmod(0o755)
    (tools / "src").symlink_to(TOOLS / "src", target_is_directory=True)
    if missing != "venv":
        (tools / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    environment = {**os.environ, "QA_EVENT_NAME": "push", "QA_PUSH_BEFORE": base}
    if missing in {"git", "bash"}:
        path = tmp_path / "path"
        path.mkdir()
        (path / "python3").symlink_to(sys.executable)
        for name in {"bash", "git"} - {missing}:
            (path / name).symlink_to(shutil.which(name))
        environment["PATH"] = str(path)
    result = subprocess.run(
        ["/bin/bash", "-c", ci_step()["run"]],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (127 if missing == "launcher" else 2)
    assert "not applicable" not in result.stdout
    if missing != "launcher":
        assert "INCONCLUSIVE" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "path, matches",
    [
        ("dev-util/adapter/adapter-1.ebuild", True),
        ("dev-util/adapter/Manifest", True),
        ("dev-util/adapter/metadata.xml", True),
        ("dev-util/adapter/files/deep/local.patch", True),
        ("metadata/md5-cache/dev-util/adapter-1", True),
        ("metadata/layout.conf", True),
        ("profiles/package.mask", True),
        ("eclass/local.eclass", True),
        (".agents/skills/overlay-tools/docs/qa-ebuild.md", False),
        ("dev-util/adapter/README.md", False),
        ("dev-util/adapter/Manifest/extra", False),
        ("metadata/update-exclusions.json", False),
    ],
)
def test_precommit_filter_is_exact(path, matches):
    config = yaml.safe_load((TOOLS.parents[2] / ".pre-commit-config.yaml").read_text())
    hook = next(h for r in config["repos"] for h in r["hooks"] if h["id"] == "qa-ebuild-info")
    assert bool(re.search(hook["files"], path)) == matches
    assert hook["verbose"] is True
    assert hook["require_serial"] is True
    assert hook["pass_filenames"] is True


@pytest.mark.parametrize("encoding", [None, "nonexistent", "UTF-32", "UTF-16"])
def test_precommit_public_runner_displays_findings_on_success(tmp_path, encoding):
    root = make_overlay(tmp_path / "repo", thin=True)
    initialize(root)
    source = root / ".agents/skills/overlay-tools"
    source.parent.mkdir(parents=True)
    launcher = public_launcher(tmp_path, "qa-ebuild-check")
    source.symlink_to(launcher.parent.parent, target_is_directory=True)
    xml = root / "dev-util/adapter/metadata.xml"
    xml.write_bytes(
        b"<broken>"
        if encoding is None
        else f'<?xml version="1.0" encoding="{encoding}"?><pkgmetadata/>'.encode("ascii")
    )
    config = yaml.safe_load((TOOLS.parents[2] / ".pre-commit-config.yaml").read_text())
    hook = next(h for r in config["repos"] for h in r["hooks"] if h["id"] == "qa-ebuild-info")
    # Replay the exact local hook without initializing the unrelated remote ruff hook.
    local_config = tmp_path / "local-precommit.yaml"
    local_config.write_text(yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]}))
    before = snapshot(root)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pre_commit",
            "run",
            "qa-ebuild-info",
            "--config",
            str(local_config),
            "--files",
            "dev-util/adapter/metadata.xml",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PRE_COMMIT_HOME": str(tmp_path / "precommit-home")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL: examined 1 package(s)" in result.stdout
    assert "authoritative QA exit: 1" in result.stdout
    assert "Invalid XML" in result.stdout
    assert "informational wrapper exit: 0" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert str(xml) in result.stdout
    assert snapshot(root) == before


def test_workflow_config_keeps_tooling_gates_and_full_history():
    workflow = yaml.load(
        (TOOLS.parents[2] / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    for event in ["push", "pull_request"]:
        paths = workflow["on"][event]["paths"]
        assert {
            "*/*/*.ebuild",
            "*/*/Manifest",
            "*/*/metadata.xml",
            "*/*/files/**",
            "metadata/md5-cache/**",
            "metadata/layout.conf",
            "profiles/**",
            "eclass/**",
            ".agents/skills/overlay-tools/**",
        }.issubset(paths)
    steps = workflow["jobs"]["overlay-tools"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == "0"
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert {"ruff check", "ruff format", "ty", "pytest"}.issubset({step["name"] for step in steps})
    step = ci_step()
    assert step["env"] == {
        "QA_EVENT_NAME": "${{ github.event_name }}",
        "QA_PR_BASE": "${{ github.event.pull_request.base.sha }}",
        "QA_PUSH_BEFORE": "${{ github.event.before }}",
    }
    assert "${{" not in step["run"]
    assert "--pkgcheck" not in step["run"]
    assert "continue-on-error" not in step
