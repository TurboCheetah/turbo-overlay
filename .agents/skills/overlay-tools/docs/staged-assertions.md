# Staged install assertions

`test-ebuild` runs Portage phases in a disposable Gentoo Docker container. Checks
inspect the resulting install image. They never launch an installed binary,
OpenRC service, init script, or other staged payload.

From the overlay root, run the OpenRC adapter smoke test with:

```sh
.agents/skills/overlay-tools/bin/test-ebuild --build --package-checks \
  --overlay-path "$PWD" dev-util/t3code-openrc/t3code-openrc-1-r1.ebuild
```

Omit `--build` to reuse the local image. This checks the init script, configuration,
libexec adapter, and package documentation directory. The registry also covers
`dev-util/t3code-openrc/t3code-openrc-1.ebuild`. It does not start a service or
exercise a per-user T3 runtime. Dependency installation and Portage configuration
writes occur inside Docker, not on the host.

## Explicit assertions

Each option is repeatable. Paths are relative to the Portage install image, not
the container root. Allowed path components contain ASCII letters, digits,
`+`, `_`, `.`, or `-`. Absolute paths, empty components, and standalone `.` or `..`
components are invalid.

| Option | Meaning |
| --- | --- |
| `--expect PATH` | Legacy presence check. Parent components must be real directories. The final entry may be a dangling symlink. |
| `--expect-executable PATH` | The image-rooted resolved target must be a regular file with at least one execute permission bit. The checker does not execute it. |
| `--expect-type TYPE:PATH` | Inspect the final entry without following its symlink. `TYPE` is `file`, `directory`, or `symlink`. Parent symlinks resolve within the image. Special files do not count as regular files. |
| `--expect-mode MODE:PATH` | Compare the resolved target's permission and special bits exactly. `MODE` is a three- or four-digit octal string, such as `755`, `0755`, or `4711`. |
| `--expect-link-target PATH=TARGET` | Require a final symlink and compare its stored link text exactly. The first `=` separates path from target. The nonempty target can contain spaces, `=`, or shell characters. It is data, not a command. Dangling targets are allowed. |
| `--expect-resolved-link PATH` | Require a final symlink whose complete image-rooted chain resolves to an existing entry. |

For example:

```sh
.agents/skills/overlay-tools/bin/test-ebuild \
  dev-util/t3code-openrc/t3code-openrc-1-r1.ebuild \
  --expect-executable etc/init.d/t3code \
  --expect-type file:etc/conf.d/t3code \
  --expect-mode 0644:etc/conf.d/t3code \
  --expect-executable usr/libexec/t3code-openrc
```

Strong checks interpret an absolute symlink target such as `/opt/app/tool` as
`opt/app/tool` inside the install image. Relative links resolve from the link's
image directory. Chains can cross symlinked parent directories. Escapes above
the image root fail, as do missing targets, non-directory parent components,
cycles, and chains that require more than 40 symlink traversals. A link to a file
that exists only on the host or in the container root does not satisfy a resolved
assertion. Exact-text link checks deliberately do not validate the target's
existence or safety; combine them with `--expect-resolved-link` when needed.

These are artifact checks, not a sandbox for hostile ebuilds. Portage phases
already execute the ebuild in the container. Check a quiescent install image.

## Opt-in package registry

`--package-checks` adds assertions from the mounted overlay's
`metadata/test-assertions.json`. Without that flag the tester does not read the
registry. Explicit assertions still apply when package checks are enabled.

The registry uses exact versioned atoms without slot or repository qualifiers.
Revisions have their own entries. There are no wildcards, ranges, implicit
fallbacks, script discovery, or shell commands.

```json
{
  "version": 1,
  "packages": {
    "=dev-util/t3code-openrc-1-r1": [
      {"kind": "type", "path": "etc/conf.d/t3code", "type": "file"},
      {"kind": "mode", "path": "etc/conf.d/t3code", "mode": "0644"},
      {"kind": "executable", "path": "usr/libexec/t3code-openrc"}
    ]
  }
}
```

Each assertion has string fields `kind` and `path`. The builtin kinds are
`exists`, `executable`, `type`, `mode`, `link-target`, and `resolved-link`.
`type`, `mode`, and `link-target` additionally require `type`, `mode`, and `target`,
respectively. No other fields are accepted. Every registered atom must have a
nonempty assertion list. The loader validates the entire registry, including
entries not selected for this run. Unknown keys, duplicate JSON keys, invalid
atoms, unsupported versions, and invalid assertions are errors.

Both the host CLI and the runner validate specifications before Docker,
dependency installation, Portage configuration writes, or phases, respectively.
The host serializes validated assertions as one JSON argv value and bind-mounts
the standard-library Python checker read-only next to `run-ebuild`. Stage3's
`python3` performs the checks. There is no shell `eval` or payload execution.
Legacy-only normal-path invocations retain their original positional transport.

## Direct runner arguments

The host CLI options above do not need a separator. When calling `docker/run-ebuild`
directly, put a standalone `--` immediately after the ebuild to select assertion
options, followed by at least one assertion spec. Without this boundary, every
remaining argument is a legacy presence path, even `--expect-mode`,
`--package-checks`, or `--assertions-json`. The runner
does not guess the argument format from path names or filesystem contents.

```sh
# Legacy presence paths, including a filename that looks like an option:
docker/run-ebuild dev-util/t3code-openrc/t3code-openrc-1-r1.ebuild \
  etc/init.d/t3code --expect-mode

# Explicit assertion options and registry checks:
docker/run-ebuild dev-util/t3code-openrc/t3code-openrc-1-r1.ebuild -- \
  --package-checks --expect-mode 0644:etc/conf.d/t3code
```

`EBUILD --` still checks the lone legacy filename `--`. With further arguments,
the boundary position reserves that name. To check it as the first of several
paths, use `EBUILD -- --expect -- OTHER_PATH`, or the host CLI's `--expect=--`.
A `--` after another legacy path remains a filename, not a boundary.

The host sends strong checks as `EBUILD -- --assertions-json JSON` and mounts
both the current runner and checker read-only, so reusing an older stage3 image
does not reuse old helper code. JSON is an exclusive assertion-input format.
Neither the runner nor the standalone helper accepts it alongside positional
assertions, assertion options, or package checks. Mixed input exits `2` before
checks, Portage configuration writes, dependency installation, or phases. The
helper's `--image`, `--ebuild`, and `--registry` remain control arguments, not
assertion specs. Combine explicit and registry checks through the host CLI or
the direct runner's non-JSON assertion mode instead.

Exit codes are `0` for a passing test, `1` for a dependency/phase/assertion failure,
and `2` for invalid arguments, a missing or malformed requested registry, an
unregistered exact target, or an unusable environment.

## Verification without Docker

From `.agents/skills/overlay-tools`:

```sh
uv sync --locked --group dev
uv run pytest tests/test_staged_assertions.py tests/test_test_ebuild.py
uv run pytest
uv run ruff check
uv run ruff format --check
uv run ty check src
```

New assertion tests invoke the actual `docker/run-ebuild` main entrypoint. Fake
Portage executables stage known filesystem fixtures and record calls. Tests cover
permission bits, entry types, exact link text, absolute and relative chains,
escapes, dangling links, cycles, the traversal bound, preflight failures, registry
opt-in behavior, JSON transport, and the OpenRC artifact checks. They do not test
private assertion helpers or execute the fixture payloads. A real Docker smoke
run remains necessary to verify the stage3 and Portage integration.
