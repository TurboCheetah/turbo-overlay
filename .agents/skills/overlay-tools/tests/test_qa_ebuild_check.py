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


def ci_workflow():
    return yaml.load(
        (TOOLS.parents[2] / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )


def concurrency_value(template, context):
    """Evaluate the small Actions expression subset used by this workflow.

    Read the actual YAML expression, rather than modeling a separate desired key.
    Unsupported syntax fails closed so a changed expression requires test review.
    """

    def value(expression):
        alternatives = expression.strip().split(" || ")
        if len(alternatives) > 1:
            result = False
            for alternative in alternatives:
                result = value(alternative)
                if result:
                    break
            return result
        operands = expression.strip().split(" && ")
        if len(operands) > 1:
            result = True
            for operand in operands:
                result = value(operand)
                if not result:
                    break
            return result
        comparison = expression.strip().split(" == ")
        if len(comparison) == 2:
            return value(comparison[0]) == value(comparison[1])
        token = expression.strip()
        if token in {"true", "false"}:
            return token == "true"
        if re.fullmatch(r"'[^']*'", token):
            return token[1:-1]
        assert re.fullmatch(r"github\.[a-z_]+", token), expression
        return context[token.removeprefix("github.")]

    exact = re.fullmatch(r"\$\{\{([^{}]*)\}\}", template)
    if exact:
        return value(exact[1])
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(value(match[1])), template)


def ci_concurrency(event, run_id, ref="refs/heads/master", workflow="ci"):
    context = {"workflow": workflow, "event_name": event, "run_id": run_id, "ref": ref}
    concurrency = ci_workflow()["concurrency"]
    return (
        concurrency_value(concurrency["group"], context),
        concurrency_value(concurrency["cancel-in-progress"], context),
    )


def test_ci_push_runs_do_not_share_a_concurrency_group():
    # Three overlapping pushes must not replace either running or pending work.
    runs = [ci_concurrency("push", run_id) for run_id in [101, 102, 103]]
    assert len({group for group, _cancel in runs}) == 3
    assert all(cancel is False for _group, cancel in runs)


def test_ci_pr_runs_still_cancel_only_the_same_pull_request():
    first = ci_concurrency("pull_request", 201, ref="refs/pull/125/merge")
    updated = ci_concurrency("pull_request", 202, ref="refs/pull/125/merge")
    other_pr = ci_concurrency("pull_request", 203, ref="refs/pull/126/merge")
    assert first == updated
    assert first[1] is True
    assert first[0] != other_pr[0]
    assert first[0] != ci_concurrency("push", 201)[0]
    assert (
        first[0]
        != ci_concurrency(
            "pull_request", 201, ref="refs/pull/125/merge", workflow="other-workflow"
        )[0]
    )


def test_ci_push_retry_reuses_only_its_own_run_group():
    assert ci_concurrency("push", 101) == ci_concurrency("push", 101)
    assert ci_concurrency("push", 101)[0] != ci_concurrency("push", 102)[0]
    assert ci_concurrency("push", 101)[1] is False


def pending_pushes(runs):
    """Model Actions' default single-pending queue, not live GitHub execution."""
    groups = {}
    for run_id, group, cancel in runs:
        if group not in groups or cancel:
            groups[group] = [run_id]
        else:
            groups[group] = [groups[group][0], run_id]
    return {run_id for members in groups.values() for run_id in members}


def test_overlapping_pushes_replay_every_distinct_package_diff(tmp_path):
    root = make_overlay(tmp_path / "repo", thin=True)
    atoms = ["dev-util/adapter", "dev-util/other", "dev-util/third"]
    for atom in atoms[1:]:
        make_overlay(root, thin=True, atom=atom)
    base = initialize(root)
    runs = []
    for index, atom in enumerate(atoms):
        # Push A introduces a finding. B and C do not modify A's package.
        (root / atom / "metadata.xml").write_text("<broken>" if index == 0 else "<pkgmetadata/>")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=QA",
            "-c",
            "user.email=qa@example.invalid",
            "commit",
            "-qm",
            f"push {index}",
        )
        tip = git(root, "rev-parse", "HEAD")
        checkout = tmp_path / f"push-{index}"
        shutil.copytree(root, checkout)
        source = checkout / ".agents/skills/overlay-tools"
        source.parent.mkdir(parents=True)
        source.symlink_to(TOOLS, target_is_directory=True)
        result = subprocess.run(
            ["bash", "-c", ci_step()["run"]],
            cwd=checkout,
            env={
                **os.environ,
                "QA_EVENT_NAME": "push",
                "QA_PUSH_BEFORE": base,
                "QA_PR_BASE": "$(touch INJECTED)",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == (1 if index == 0 else 0), result.stdout + result.stderr
        assert f"{'FAIL' if index == 0 else 'PASS'}: examined 1 package(s)" in result.stdout
        assert atom in result.stdout
        assert all(other not in result.stdout for other in atoms if other != atom)
        assert not (checkout / "INJECTED").exists()
        group, cancel = ci_concurrency("push", 101 + index)
        runs.append((101 + index, group, cancel))
        base = tip

    assert pending_pushes(runs) == {101, 102, 103}
    # These controls prove why merely disabling active cancellation is insufficient.
    assert pending_pushes([(n, "ci-master", True) for n in [101, 102, 103]]) == {103}
    assert pending_pushes([(n, "ci-master", False) for n in [101, 102, 103]]) == {101, 103}


def ci_step():
    workflow = ci_workflow()
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
