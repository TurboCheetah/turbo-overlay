# T3 Code OpenRC integration implementation plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Package a configurable OpenRC adapter for T3-owned server runtimes and deprecate both overlay desktop packages without uninstalling anything.

**Architecture:** Portage owns the adapter and system dependencies, never the per-user T3 runtime. OpenRC supervises the upstream protocol-3 `t3 __service-launcher`; the wrapper resolves `activeVersion` each time it starts. Desktop packages remain available but masked, with explicit migration instructions and update automation exclusions.

**Tech stack:** EAPI 8, POSIX shell, jq, pytest subprocess tests, existing Python overlay-tools, pkgcheck, real Portage phases.

**Base:** 6ff1d104dac70984bb55a6d1deb4c96a214a2fcc. Worktree `/home/turbo/.local/src/.turbo/t3code-openrc`, branch `feat/t3code-openrc`. Preserve the other checkout's branch and dirty cache files. No push, real emerge, live service deployment, runtime mutation, uninstall, or automatic startup.

## Accepted specification

- Add `dev-util/t3code-openrc/t3code-openrc-1.ebuild`, metadata and Manifest.
- Install `/etc/init.d/t3code`, `/etc/conf.d/t3code`, and `/usr/libexec/t3code-openrc` plus package documentation. Do not install T3 itself, fetch a nightly, or claim user runtime files.
- Use the existing maintainer identity `turbo <dev@turbo.ooo>` from T3 metadata. Adapter code is GPL-2. No remote-id that would make updater confuse adapter version 1 with T3 releases.
- Configure an explicit non-root user and T3 home through conf.d. Do not bake this host's user, home, port, or tailnet name into package defaults.
- Wrapper public commands: `check`, `run`, and `initialize EXACT_VERSION`. `check` validates JSON protocol 3, exact SemVer activeVersion, executable and matching sentinel, and performs no writes or execution of the app. `run` refuses root and execs the active standalone runtime's `__service-launcher` with explicit safe environment. `initialize` is an explicit unprivileged bootstrap for an already-installed, complete upstream runtime, creates only a missing protocol-3 state file, and refuses to replace existing state. No download or installer execution.
- The OpenRC hook checks the runtime as the configured service user, using the fixed root-owned helper, without executing app-controlled files as root. Use `depend()` and `supervisor="supervise-daemon"`, bounded respawn, no `command_background`.
- Set service HOME and PATH deliberately; include the account's `.local/bin`. Default network exposure is loopback and Tailscale disabled. Optional Tailscale needs explicit HTTPS port and loopback target, guarded readiness/retries, and exactly one owner. No root logs/chown operations on user-controlled app paths; log creation/redirection occurs only after privilege drop.
- Hooks must propagate missing runtime, configuration and proxy failures, without modifying runtime or state. Bootstrap is not a side effect of emerge or service startup.
- Retain all six existing desktop ebuilds. Add explanatory deprecation masks for `dev-util/t3code-bin` and `dev-util/t3code-nightly-bin` in `profiles/package.mask`.
- Add explicit repository update exclusions in `metadata/update-exclusions.json`. Automatic check-updates must skip both retired desktop packages and the locally versioned adapter before upstream API calls. update-ebuild must reject excluded packages before mutation. Ordinary package discovery and unrelated checks remain unchanged; missing exclusions file means no exclusions. Invalid configuration fails clearly rather than silently bypassing policy.
- README, the package guide and overlay-tools documentation explain which component each updater owns, upstream desktop migration, runtime bootstrap, package masking versus uninstall, and local custom-service replacement/config-protect review. No claim that this package has been installed or the deprecations published.
- Scope note: the AGENTS.md edit was initially denied and later approved for this PR, which adds the "T3 Code update ownership" note there.

## Approved test seams

Public wrapper subprocess commands, exported environment handed to a fixture executable, public OpenRC `depend`/`start_pre`/`stop_post` hooks with command-boundary fixtures, existing check-updates/update-ebuild CLI boundaries, and the actual EAPI install image. These prove adapter behavior and policy, not a future upstream release. Do not run fixture processes against the real T3 home.

## Task 1: Adapter runtime and service

Files: new package under `dev-util/t3code-openrc/`; tests `.agents/skills/overlay-tools/tests/test_t3code_openrc.py`.

1. Write a subprocess test for a missing helper or unsupported state; run it and record failure.
2. Implement the minimum wrapper for check/run, then add each edge case as a red-green slice: incompatible protocol, malformed JSON, path traversal/dist-tag, absent executable, sentinel mismatch, root refusal, exact runtime selection, safe environment, no writes in check, future activeVersion read at restart.
3. Add explicit initialize behavior and test no overwrite, missing/incomplete runtime, root refusal, private atomic state write.
4. Add configurable POSIX OpenRC script/conf.d. Test required user/home settings, dependencies, unprivileged preflight, optional proxy enable/disable and bounded failure, same exported configuration in preflight/run.
5. Add source-less adapter ebuild, existing maintainer metadata and package guide. Run shell syntax/shellcheck and focused pytest.
6. Do not commit until controller reviews scope and integration.

Example public seam:
```python
result = subprocess.run([str(helper), "check"], env=fixture_env, capture_output=True, text=True)
assert result.returncode != 0
assert "protocol" in result.stderr.lower()
```

## Task 2: Deprecation and update ownership

Files: `profiles/package.mask`, `metadata/update-exclusions.json`, `.agents/skills/overlay-tools/src/overlay_tools/core/update_policy.py`, check-updates/update-ebuild CLI integration, focused policy tests, README, AGENTS and overlay-tools command documentation.

1. Write CLI tests that demonstrate an excluded package currently triggers an upstream check or permits a bump; run red.
2. Add narrowly scoped policy loading and enforce it before network/mutation. Test missing file, malformed configuration, unrelated packages, full scan and explicit package requests.
3. Add masks and explanations, retaining current ebuilds and manifests unchanged.
4. Update migration guidance without claiming a Portage atom rename: an adapter does not replace the desktop application. Document manual upstream installation as the user, no installer from pkg_postinst, and no automatic unmerge/start/restart.
5. Run focused tests, lint and typecheck. Do not commit or edit Task 1 package/tests.

## Task 3: Spec and quality review

1. Controller inspects all modified/new files and reruns focused tests.
2. Obtain independent spec review against this document; fix gaps and rerun until PASS.
3. Obtain independent standards/security/quality review after spec PASS; fix blocking findings.
4. Verify tests actually execute the shell helper and hooks, not copied internal implementation.

## Task 4: Real package verification and local commit

1. Generate Manifest for the exact new ebuild.
2. Run real clean/setup/unpack/prepare/configure/compile/install phases into a scratch build area, with no merge into the host.
3. Inspect installed files, modes and absence of any T3 runtime or data.
4. Run pkgcheck against the changed tree and attribute any baseline diagnostics separately.
5. Run full overlay-tools pytest, ruff check/format check, ty check, shellcheck and git diff --check.
6. Commit only intended files locally, with the existing repository author identity. Report commit, tests and scope. No push or installed-package removal; deployment is a separate approval.
