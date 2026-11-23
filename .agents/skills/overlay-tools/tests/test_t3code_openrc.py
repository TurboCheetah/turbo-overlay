"""Approved seams: helper subprocesses and public OpenRC hooks with command fixtures."""

import json
import os
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
FILES = ROOT / "dev-util/t3code-openrc/files"
VERSION = "0.0.46-nightly.202610072774"

UPDATED_VERSION = "0.1.12-nightly.20261120.4994"
COMMITTED_UPDATE = {
    "protocol": 4,
    "activeVersion": UPDATED_VERSION,
    "update": {
        "id": "fixture-update",
        "fromVersion": VERSION,
        "targetVersion": UPDATED_VERSION,
        "status": "committed",
    },
}


@pytest.fixture(params=["", "-r4"], ids=["legacy-r2-r3", "r4"])
def adapter(request):
    suffix = request.param
    return FILES / f"t3code-openrc{suffix}", FILES / f"t3code.initd{suffix}"


@pytest.fixture
def invoke(adapter):
    return partial(invoke_helper, adapter[0])


@pytest.fixture
def hook(adapter):
    return partial(invoke_hook, adapter)


@pytest.fixture
def sandbox():
    # Never use the account's actual ~/.t3. Prefer an existing TMPDIR (a local
    # harness sets one), otherwise fall back to the platform default so the
    # suite stays portable on CI runners.
    candidate = os.environ.get("TMPDIR")
    base = candidate if candidate and Path(candidate).is_dir() else None
    with tempfile.TemporaryDirectory(prefix="t3-openrc-", dir=base) as directory:
        yield Path(directory)


@pytest.fixture
def home(sandbox):
    result = sandbox / "account with spaces" / ".t3"
    result.mkdir(parents=True)
    return result


def invoke_helper(helper, home, *args, extra=None):
    env = dict(os.environ, T3CODE_HOME=str(home), HOME=str(home.parent))
    env.update(extra or {})
    return subprocess.run(
        ["/bin/sh", str(helper), *args], env=env, capture_output=True, text=True, check=False
    )


def runtime(home, version=VERSION):
    directory = home / "runtime" / "versions" / version
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "t3"
    executable.write_text("#!/bin/sh\nprintf 'unexpected execution' >&2\nexit 99\n")
    executable.chmod(0o755)
    (directory / ".install-complete").write_text(version + "\n")
    return directory


def state(home, value=None):
    path = home / "runtime" / "service-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value or {"protocol": 3, "activeVersion": VERSION}))
    return path


def test_check_accepts_complete_runtime_without_writes_or_execution(home, adapter, invoke):
    value = COMMITTED_UPDATE if adapter[0].name.endswith("-r4") else None
    version = value["activeVersion"] if value else VERSION
    runtime(home, version)
    state(home, value)
    before = {
        p: (p.stat().st_mode, p.stat().st_mtime_ns, p.read_bytes())
        for p in home.rglob("*")
        if p.is_file()
    }
    result = invoke(home, "check")
    assert result.returncode == 0, result.stderr
    assert result.stdout == version + "\n"
    after = {
        p: (p.stat().st_mode, p.stat().st_mtime_ns, p.read_bytes())
        for p in home.rglob("*")
        if p.is_file()
    }
    assert after == before
    assert not (home / "userdata").exists()


@pytest.mark.parametrize("version", ["nightly", "../escape", "1.2", "01.2.3", "1.2.3-01", "^1.2.3"])
def test_check_rejects_nonexact_versions(home, version, invoke):
    state(home, {"protocol": 3, "activeVersion": version})
    result = invoke(home, "check")
    assert result.returncode != 0
    assert "version" in result.stderr.lower()


@pytest.mark.parametrize("damage", ["missing", "nonexecutable", "sentinel", "mismatch", "json"])
def test_check_rejects_damaged_runtime(home, damage, invoke):
    directory = runtime(home)
    path = state(home)
    if damage == "missing":
        (directory / "t3").unlink()
    elif damage == "nonexecutable":
        (directory / "t3").chmod(0o644)
    elif damage == "sentinel":
        (directory / ".install-complete").unlink()
    elif damage == "mismatch":
        (directory / ".install-complete").write_text("0.0.1\n")
    else:
        path.write_text("{broken")
    assert invoke(home, "check").returncode != 0


@pytest.mark.parametrize("adapter", ["-r4"], indirect=True)
@pytest.mark.parametrize("token", ["NaN", "Infinity", "01", "+1", ".1", "1."])
def test_check_rejects_non_json_tokens_accepted_by_jq(home, token, invoke):
    runtime(home, UPDATED_VERSION)
    path = state(home, COMMITTED_UPDATE)
    path.write_text(path.read_text().replace('"fixture-update"', token))
    result = invoke(home, "check")
    assert result.returncode != 0


def recording_runtime(home, version=VERSION):
    directory = runtime(home, version)
    (directory / "t3").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$T3CODE_HOME/args"\n'
        '/usr/bin/env > "$T3CODE_HOME/environment"\n'
        'printf "fixture output\\n"\n'
    )
    return directory


def test_run_resolves_active_version_and_sets_safe_environment(home, adapter, invoke):
    recording_runtime(home)
    path = state(home)
    extra = {
        "T3CODE_HOST": "0.0.0.0",
        "T3CODE_NO_BROWSER": "false",
        "T3CODE_MODE": "desktop",
        "T3CODE_AUTO_BOOTSTRAP_PROJECT_FROM_CWD": "true",
        "T3CODE_TAILSCALE_SERVE": "true",
        "T3CODE_PORT": "4321",
    }
    first = invoke(home, "run", extra=extra)
    assert first.returncode == 0, first.stderr
    assert (home / "args").read_text().splitlines() == ["__service-launcher"]
    env = dict(line.split("=", 1) for line in (home / "environment").read_text().splitlines())
    assert env["T3CODE_HOST"] == "127.0.0.1"
    assert env["T3CODE_MODE"] == "web"
    assert env["T3CODE_NO_BROWSER"] == "true"
    assert env["T3CODE_AUTO_BOOTSTRAP_PROJECT_FROM_CWD"] == "false"
    assert env["T3CODE_TAILSCALE_SERVE"] == "false"
    assert env["T3CODE_PORT"] == "4321"
    assert env["HOME"] == str(home.parent)
    assert env["PATH"].startswith(str(home.parent / ".local/bin") + ":")
    assert "fixture output" in (home / "userdata/logs/openrc.log").read_text()
    updated = (
        COMMITTED_UPDATE
        if adapter[0].name.endswith("-r4")
        else {"protocol": 3, "activeVersion": "1.2.3+build.4"}
    )
    version = updated["activeVersion"]
    recording_runtime(home, version)
    path.write_text(json.dumps(updated))
    before = (path.stat().st_mtime_ns, path.read_bytes())
    (home / "runtime/versions" / VERSION / "t3").unlink()
    assert invoke(home, "run").returncode == 0
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before
    (home / "runtime/versions" / version / ".install-complete").unlink()
    assert "incomplete" in invoke(home, "run").stderr
    path.write_text('{"protocol":2,"activeVersion":"1.2.3"}')
    assert invoke(home, "run").returncode != 0


@pytest.mark.parametrize("action", ["run", "initialize"])
def test_mutating_commands_refuse_root(home, sandbox, action, invoke):
    commands = sandbox / "commands"
    commands.mkdir()
    fake_id = commands / "id"
    fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n")
    fake_id.chmod(0o755)
    runtime(home)
    result = invoke(
        home,
        action,
        *([VERSION] if action == "initialize" else []),
        extra={"PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert "root" in result.stderr.lower()
    assert not (home / "userdata").exists()
    assert not (home / "runtime/service-state.json").exists()


def test_initialize_creates_private_protocol_three_state(home, invoke):
    runtime(home)
    result = invoke(home, "initialize", VERSION)
    assert result.returncode == 0, result.stderr
    path = home / "runtime/service-state.json"
    assert json.loads(path.read_text()) == {"protocol": 3, "activeVersion": VERSION}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.getuid()
    assert sorted(p.name for p in path.parent.iterdir()) == ["service-state.json", "versions"]
    assert invoke(home, "check").returncode == 0
    assert not (home / "userdata").exists()


@pytest.mark.parametrize("existing", ["file", "directory", "dangling-link", "link"])
def test_initialize_never_replaces_existing_state(home, sandbox, existing, invoke):
    runtime(home)
    path = home / "runtime/service-state.json"
    target = sandbox / "target"
    target.write_text("do not overwrite")
    if existing == "file":
        path.write_text("invalid but existing")
    elif existing == "directory":
        path.mkdir()
    else:
        path.symlink_to(target if existing == "link" else sandbox / "absent")
    result = invoke(home, "initialize", VERSION)
    assert result.returncode != 0
    assert "existing" in result.stderr.lower()
    assert target.read_text() == "do not overwrite"
    if existing == "file":
        assert path.read_text() == "invalid but existing"
    elif existing == "directory":
        assert list(path.iterdir()) == []
    else:
        assert path.is_symlink()


@pytest.mark.parametrize("version", ["nightly", "../escape", "1.2.3", VERSION])
def test_initialize_refuses_missing_or_incomplete_runtime(home, version, invoke):
    if version == VERSION:
        (runtime(home) / ".install-complete").unlink()
    assert invoke(home, "initialize", version).returncode != 0
    assert not (home / "runtime/service-state.json").exists()


def invoke_hook(adapter, home, sandbox, action="start_pre", config=None, su_status=0):
    helper, init = adapter
    commands = sandbox / "hook commands"
    commands.mkdir(exist_ok=True)
    trace = sandbox / "commands.jsonl"
    # Fixtures stand at system command boundaries, never inside adapter logic.
    fixture = (
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "with open(os.environ['TRACE'], 'a') as f:\n"
        "    f.write(json.dumps({'command': name, 'args': sys.argv[1:], "
        "'env': dict(os.environ)}) + '\\n')\n"
        "if name == 'getent':\n"
        "    print('serviceaccount:x:1234:1234::' + os.environ['ACCOUNT_HOME'] + ':/bin/sh')\n"
        "elif name == 'su':\n"
        "    if os.environ.get('SU_STATUS') != '0': sys.exit(int(os.environ['SU_STATUS']))\n"
        "    sys.exit(subprocess.call(['/bin/sh', os.environ['TEST_HELPER'], 'check']))\n"
        "elif name == 'tailscale':\n"
        "    if sys.argv[1] == 'status':\n"
        "        print(json.dumps({'BackendState': os.environ.get('BACKEND_STATE', 'Running')}))\n"
        "    elif sys.argv[2] == 'status':\n"
        "        port = os.environ.get('t3code_tailscale_https_port', '9443')\n"
        "        print(json.dumps({'TCP': {port: {'HTTPS': True}}, 'Web': {\n"
        "            'fixture.ts.net:' + port: {'Handlers': {'/': {\n"
        "                'Proxy': os.environ.get('t3code_tailscale_target', '')}}}}}))\n"
        "    elif 'off' not in sys.argv: sys.exit(int(os.environ.get('SERVE_STATUS', '0')))\n"
        "elif name == 'curl':\n"
        '    print(\'{"serverVersion":"1.2.3"}\')\n'
        "    sys.exit(int(os.environ.get('CURL_STATUS', '0')))\n"
    )
    for name in ["getent", "su", "tailscale", "curl", "sleep"]:
        path = commands / name
        path.write_text(fixture)
        path.chmod(0o755)
    settings = {"t3code_user": "serviceaccount", "t3code_home": str(home)}
    settings.update(config or {})
    env = dict(
        os.environ,
        PATH=f"{commands}:/usr/bin:/bin",
        TRACE=str(trace),
        ACCOUNT_HOME=str(home.parent),
        TEST_HELPER=str(helper),
        SU_STATUS=str(su_status),
        **settings,
    )
    script = (
        'eerror() { printf "%s\\n" "$*" >&2; }\n'
        'need() { printf "need %s\\n" "$*"; }\n'
        'use() { printf "use %s\\n" "$*"; }\n'
        'after() { printf "after %s\\n" "$*"; }\n'
        '. "$1"\n'
        '"$2" || exit $?\n'
        'if [ "$2" = start_pre ]; then\n'
        ' printf "command=%s\\nargs=%s\\nuser=%s\\ndirectory=%s\\nsupervisor=%s\\n" '
        '"$command" "$command_args" "$command_user" "$directory" "$supervisor"\n'
        " /usr/bin/env\nfi\n"
    )
    result = subprocess.run(
        ["/bin/sh", "-c", script, "hooks", str(init), action],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    return result, events


def test_preflight_uses_fixed_helper_as_service_user_and_exports_launch_environment(
    home, sandbox, adapter, hook
):
    value = COMMITTED_UPDATE if adapter[0].name.endswith("-r4") else None
    runtime(home, value["activeVersion"] if value else VERSION)
    state(home, value)
    result, events = hook(home, sandbox, config={"t3code_port": "4321"})
    assert result.returncode == 0, result.stderr
    su = next(event for event in events if event["command"] == "su")
    assert su["args"] == [
        "-s",
        "/bin/sh",
        "-m",
        "-c",
        "exec /usr/libexec/t3code-openrc check",
        "--",
        "serviceaccount",
    ]
    assert su["env"]["HOME"] == str(home.parent)
    assert su["env"]["T3CODE_HOME"] == str(home)
    assert su["env"]["T3CODE_PORT"] == "4321"
    for name, value in {
        "T3CODE_HOST": "127.0.0.1",
        "T3CODE_MODE": "web",
        "T3CODE_NO_BROWSER": "true",
        "T3CODE_TAILSCALE_SERVE": "false",
        "T3CODE_AUTO_BOOTSTRAP_PROJECT_FROM_CWD": "false",
    }.items():
        assert su["env"][name] == value
        assert f"{name}={value}" in result.stdout
    assert "command=/usr/libexec/t3code-openrc" in result.stdout
    assert "args=run" in result.stdout
    assert "user=serviceaccount" in result.stdout
    assert "supervisor=supervise-daemon" in result.stdout
    assert not (home / "userdata").exists()
    assert not any(e["command"] == "tailscale" for e in events)
    stopped, events = hook(home, sandbox, "stop_post")
    assert stopped.returncode == 0
    assert not any(e["command"] == "tailscale" for e in events)


@pytest.mark.parametrize(
    "config",
    [
        {"t3code_user": ""},
        {"t3code_home": ""},
        {"t3code_user": "root"},
        {"t3code_home": "relative"},
        {"t3code_port": "0"},
    ],
)
def test_preflight_refuses_invalid_configuration_before_su(home, sandbox, config, hook):
    result, events = hook(home, sandbox, config=config)
    assert result.returncode != 0
    assert not any(event["command"] == "su" for event in events)


def test_preflight_propagates_unprivileged_check_failure(home, sandbox, adapter, hook):
    result, events = hook(home, sandbox, su_status=17)
    assert result.returncode != 0
    if adapter[0].name.endswith("-r4"):
        assert "initialize" not in result.stderr.lower()
    assert any(event["command"] == "su" for event in events)
    assert not (home / "userdata").exists()


def test_openrc_dependencies(home, sandbox, hook):
    result, events = hook(home, sandbox, "depend")
    assert result.returncode == 0
    assert "need net" in result.stdout
    assert "tailscale" not in result.stdout
    assert not events


PROXY = {
    "t3code_tailscale": "true",
    "t3code_tailscale_https_port": "9443",
    "t3code_tailscale_target": "http://127.0.0.1:3773",
    "t3code_proxy_retries": "2",
}


def test_proxy_mapping_has_only_openrc_lifecycle_owner(home, sandbox, hook):
    runtime(home)
    state(home)
    pre, events = hook(home, sandbox, config=PROXY)
    assert pre.returncode == 0, pre.stderr
    assert any(e["command"] == "tailscale" and e["args"] == ["status", "--json"] for e in events)
    assert not any(e["command"] == "tailscale" and e["args"][0] == "serve" for e in events)
    post, events = hook(home, sandbox, "start_post", PROXY)
    assert post.returncode == 0, post.stderr
    assert any(
        e["command"] == "curl"
        and PROXY["t3code_tailscale_target"] + "/.well-known/t3/environment" in e["args"]
        for e in events
    )
    assert any(
        e["command"] == "tailscale"
        and e["args"] == ["serve", "--bg", "--https=9443", "http://127.0.0.1:3773"]
        for e in events
    )
    assert any(
        e["command"] == "tailscale" and e["args"] == ["serve", "status", "--json"] for e in events
    )
    stop, events = hook(home, sandbox, "stop_post", PROXY)
    assert stop.returncode == 0, stop.stderr
    assert any(
        e["command"] == "tailscale" and e["args"] == ["serve", "--https=9443", "off"]
        for e in events
    )
    dependency, _ = hook(home, sandbox, "depend", PROXY)
    assert "need tailscale" in dependency.stdout.splitlines()


@pytest.mark.parametrize(
    "override",
    [
        {"t3code_tailscale_https_port": ""},
        {"t3code_tailscale_target": ""},
        {"t3code_tailscale_target": "http://0.0.0.0:3773"},
        {"t3code_tailscale_target": "http://127.0.0.1:9999"},
        {"t3code_proxy_retries": "100000"},
    ],
)
def test_proxy_rejects_unsafe_or_implicit_configuration(home, sandbox, override, hook):
    result, events = hook(home, sandbox, config=PROXY | override)
    assert result.returncode != 0
    assert result.stderr
    assert not any(e["command"] in {"su", "tailscale"} for e in events)


@pytest.mark.parametrize(
    "action,override,command",
    [
        ("start_pre", {"BACKEND_STATE": "Starting"}, "tailscale"),
        ("start_post", {"CURL_STATUS": "7"}, "curl"),
        ("start_post", {"SERVE_STATUS": "1"}, "tailscale"),
    ],
)
def test_proxy_failures_are_bounded_and_propagated(home, sandbox, action, override, command, hook):
    runtime(home)
    state(home)
    result, events = hook(home, sandbox, action, PROXY | override)
    assert result.returncode != 0
    assert result.stderr
    calls = [e for e in events if e["command"] == command]
    assert 1 <= len(calls) <= 2


@pytest.mark.parametrize(
    "value",
    [
        {"protocol": "3", "activeVersion": VERSION},
        {"protocol": "4", "activeVersion": VERSION},
        {"protocol": 5, "activeVersion": VERSION},
        {"protocol": 4, "activeVersion": "nightly"},
        [],
        {"protocol": 3},
        {"protocol": 3, "activeVersion": "1.2.3\n"},
        {"protocol": 3, "activeVersion": "bad\n1.2.3"},
    ],
)
def test_check_rejects_invalid_state_shapes_and_embedded_newlines(home, value, invoke):
    runtime(home)
    state(home).write_text(json.dumps(value))
    if isinstance(value, dict) and isinstance(value.get("activeVersion"), str):
        runtime(home, value["activeVersion"])
    assert invoke(home, "check").returncode != 0


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-alpha.0", "1.2.3-1a+build.09"])
def test_exact_semver_accepts_upstream_prerelease_and_build_forms(home, version, invoke):
    runtime(home, version)
    state(home, {"protocol": 3, "activeVersion": version})
    assert invoke(home, "check").returncode == 0


def test_initialize_rejects_multiline_version_even_if_directory_exists(home, invoke):
    version = "1.2.3\n"
    runtime(home, version)
    assert invoke(home, "initialize", version).returncode != 0
    assert not (home / "runtime/service-state.json").exists()


def test_initialize_race_has_one_private_complete_winner(home, adapter):
    runtime(home)
    env = dict(os.environ, T3CODE_HOME=str(home))
    processes = [
        subprocess.Popen(
            ["/bin/sh", str(adapter[0]), "initialize", VERSION],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for process in processes:
        process.communicate(timeout=10)
    assert sorted(process.returncode for process in processes) == [0, 1]
    path = home / "runtime/service-state.json"
    assert json.loads(path.read_text()) == {"protocol": 3, "activeVersion": VERSION}
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(".openrc-state.*"))


def test_user_paths_with_repeated_spaces_and_shell_characters_stay_literal(sandbox, invoke, hook):
    home = sandbox / "user  'quoted' $(touch OWNED); $HOME" / ".t3"
    home.mkdir(parents=True)
    recording_runtime(home)
    state(home)
    result, _ = hook(home, sandbox)
    assert result.returncode == 0, result.stderr
    # Only a constant reaches OpenRC's root-side eval-based supervisor.
    assert "directory=/\n" in result.stdout
    run = invoke(home, "run")
    assert run.returncode == 0, run.stderr
    env = dict(line.split("=", 1) for line in (home / "environment").read_text().splitlines())
    assert env["HOME"] == str(home.parent)
    assert env["PWD"] == str(home.parent)
    assert not (home.parent / "OWNED").exists()
    assert not (sandbox / "OWNED").exists()


def test_run_fails_closed_if_uid_lookup_fails(home, sandbox, invoke):
    commands = sandbox / "broken-id"
    commands.mkdir()
    path = commands / "id"
    path.write_text("#!/bin/sh\nexit 1\n")
    path.chmod(0o755)
    recording_runtime(home)
    state(home)
    result = invoke(home, "run", extra={"PATH": f"{commands}:/usr/bin:/bin"})
    assert result.returncode != 0
    assert not (home / "userdata").exists()


def test_check_rejects_protocol_two(home, invoke):
    runtime = home / "runtime"
    runtime.mkdir()
    (runtime / "service-state.json").write_text(
        json.dumps({"protocol": 2, "activeVersion": VERSION})
    )
    result = invoke(home, "check")
    assert result.returncode != 0
    assert "protocol" in result.stderr.lower()


def test_check_rejects_fifo_state_without_hanging(home, adapter):
    """A FIFO must be rejected instead of blocking OpenRC's preflight forever."""
    runtime(home)
    path = home / "runtime/service-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    env = dict(os.environ, T3CODE_HOME=str(home), HOME=str(home.parent))
    try:
        result = subprocess.run(
            ["/bin/sh", str(adapter[0]), "check"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("check blocked on a FIFO state file") from exc
    assert result.returncode != 0
    assert "state" in result.stderr.lower()

    assert "initialize" not in result.stderr.lower()


@pytest.mark.parametrize("adapter", ["-r4"], indirect=True)
@pytest.mark.parametrize("dangling", [False, True], ids=["regular-target", "dangling"])
def test_r4_check_rejects_final_state_symlink(home, sandbox, invoke, dangling):
    runtime(home)
    path = state(home)
    target = sandbox / "external-state.json"
    path.rename(target)
    if dangling:
        target.unlink()
    path.symlink_to(target)
    result = invoke(home, "check")
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert "initialize" not in result.stderr.lower()
    assert path.is_symlink()


@pytest.mark.parametrize("adapter", ["-r4"], indirect=True)
def test_r4_preflight_passes_through_fresh_setup_hint(home, sandbox, hook):
    runtime(home)
    result, events = hook(home, sandbox)
    assert result.returncode != 0
    assert any(event["command"] == "su" for event in events)
    assert "helper error" in result.stderr
    assert "fresh setup" in result.stderr
    assert "initialize EXACT_VERSION" in result.stderr
    assert "service user" in result.stderr
    assert not (home / "runtime/service-state.json").exists()


@pytest.mark.parametrize("adapter", ["-r4"], indirect=True)
@pytest.mark.parametrize("case", ["malformed", "unsupported", "incomplete", "inaccessible"])
def test_r4_check_omits_fresh_setup_hint_for_other_failures(home, invoke, case):
    directory = runtime(home)
    path = state(home)
    if case == "malformed":
        path.write_text("{broken")
    elif case == "unsupported":
        state(home, {"protocol": 5, "activeVersion": VERSION})
    elif case == "incomplete":
        (directory / ".install-complete").unlink()
    elif os.geteuid() == 0:
        pytest.skip("root can bypass directory permissions")
    else:
        path.parent.chmod(0)
    try:
        result = invoke(home, "check")
    finally:
        if case == "inaccessible":
            path.parent.chmod(0o700)
    assert result.returncode != 0
    assert "initialize" not in result.stderr.lower()
