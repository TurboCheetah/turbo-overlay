# overlay-tools

Shared Python library for turbo-overlay maintenance scripts.

## Installation

No installation required. Scripts use `uv run` to manage dependencies automatically.

**Prerequisite**: Install [uv](https://github.com/astral-sh/uv)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Usage

### Check for Updates

```bash
.agents/skills/overlay-tools/bin/check-updates

# Restrict by release channel (derived from MY_PV)
.agents/skills/overlay-tools/bin/check-updates --channel nightly
.agents/skills/overlay-tools/bin/check-updates --exclude-channel nightly
```

All scans, including explicit `--package` targets, honor the repository's
[`metadata/update-exclusions.json`](../../../metadata/update-exclusions.json)
before channel selection or network lookup. Skipped atoms and their reasons
are printed on stderr, even with `--json`, and are omitted from results.
Exit `0` means an eligible update exists, `1` means an error, and `2` means no
eligible updates, including a scan where every package is excluded.

Both deprecated T3 desktop packages and the locally versioned OpenRC adapter
are excluded. See the [migration guide](../../../README.md#t3-code-desktop-and-openrc).
The adapter is not a desktop replacement and must not be bumped from upstream
runtime releases or nightly tags. Daily and weekly automation schedules remain
unchanged.

### Bump Package Version

```bash
.agents/skills/overlay-tools/bin/update-ebuild -v 1.2.3 category/package

# With PR creation
.agents/skills/overlay-tools/bin/update-ebuild --pr -v 1.2.3 category/package

# With MY_PV and upstream URL
.agents/skills/overlay-tools/bin/update-ebuild --pr -v 1.2.3 -m "1.2.3" --upstream-url "https://..." category/package
```

`update-ebuild` rejects an excluded package with exit `1` and its reason before
any ebuild or Manifest write, fetch, branch change, commit, or push. This also
applies to `--dry-run`, `--pr`, `--yes`, and `--skip-git`; those flags do not
override repository policy.

### Repository update policy

`metadata/update-exclusions.json` is a JSON object mapping exact,
unversioned `category/package` atoms to non-empty explanation strings:

```json
{
  "dev-util/example-bin": "Deprecated; use the upstream user installation."
}
```

A missing file means no exclusions, so other overlays retain existing behavior.
Malformed JSON, non-object data, duplicate atoms, invalid atom syntax, and
non-string or blank reasons cause both commands to fail closed with exit `1`.
Do not use versions, operators, wildcards, slots, or repository qualifiers in
keys. Validate the whole policy before checking or updating any package.

This file controls maintenance automation only. `profiles/package.mask`
controls Portage package selection independently; a mask alone does not stop
updates, and an update exclusion does not mask or uninstall a package.

### Test an exact ebuild in Docker

```bash
# Builds a reusable local image from Gentoo stage3 with a Gentoo repo snapshot.
.agents/skills/overlay-tools/bin/test-ebuild --build \
  --overlay-path /path/to/pr-checkout \
  --expect usr/bin/t3code \
  dev-util/t3code-nightly-bin/t3code-nightly-bin-0.0.43_pre202609272344.ebuild

# Subsequent tests reuse the image and can target any overlay checkout.
.agents/skills/overlay-tools/bin/test-ebuild \
  --overlay-path /path/to/pr-checkout \
  dev-util/t3code-nightly-bin/t3code-nightly-bin-0.0.43_pre202609272344.ebuild
```

`--build` creates `turbo-overlay/ebuild-test:local` from a freshly pulled
stage3 and Portage snapshot (`--pull --no-cache`); subsequent runs reuse it,
and fail with exit 2 if it has not been built. The image intentionally tracks
`latest`, so results can shift between rebuilds. The checkout and
`docker/run-ebuild` are mounted read-only on every run, so script changes apply
without a rebuild. Build dependencies (`DEPEND`/`BDEPEND`, e.g. Go for
`go-module` ebuilds) are emerged from the Gentoo tree first. Portage fetches the distfile, validates the
Manifest, and runs `unpack`, `prepare`, `configure`, `compile`, and `install` into
a disposable container image directory. Repeat `--expect RELATIVE/PATH` to
assert paths exist in that staging image; a final symlink counts as present
even if its absolute target only exists on an installed system. Exit nonzero
on any phase or assertion failure.
Portage's network sandbox cannot run in an unprivileged container, so build
phases have network access; an ebuild that downloads during `src_compile`
passes here but fails under a real `emerge`.
It does **not** install runtime dependencies, run the GUI, or prove the package
works on a real desktop; missing-library QA notices from a bare stage3 need
confirmation after a full dependency install. Only test trusted ebuilds:
Docker is not a sandbox for hostile package code.

### Review new PRs

The [PR autopilot](autopilot/README.md) contains the deployed review worker's
filter, monitor, merge gate, and tests. The signed webhook and Hermes job
configuration are local and are **not** committed. External forks get review
but require your approval before merge.

## Development

```bash
cd .agents/skills/overlay-tools

# Install dev tools (pytest, ruff, ty, pre-commit)
uv sync --group dev

# Lint, format, type check, test
uv run ruff check .
uv run ruff format .
uv run ty check src
uv run pytest -q
```


## Architecture

```
overlay-tools/
├── bin/                        # check-updates, update-ebuild, test-ebuild wrappers
├── docker/
│   ├── Dockerfile              # Gentoo stage3 image for test-ebuild
│   └── run-ebuild              # Container entrypoint: Portage phases + assertions
├── src/overlay_tools/
│   ├── cli/
│   │   ├── check_updates.py    # check-updates CLI
│   │   ├── test_ebuild.py      # test-ebuild CLI
│   │   └── update_ebuild.py    # update-ebuild CLI
│   └── core/
│       ├── ebuilds.py          # Ebuild parsing
│       ├── errors.py           # Custom exceptions
│       ├── gh_utils.py         # GitHub CLI (gh) wrapper
│       ├── github.py           # GitHub API client
│       ├── git_utils.py        # Git operations
│       ├── logging.py          # Rich logging
│       ├── overlay.py          # Package discovery
│       ├── report.py           # Output formatting
│       ├── subprocess_utils.py # Shell commands
│       ├── update_policy.py    # Explicit repository update exclusions
│       └── versions.py         # Version handling
└── tests/
```

## Checkout context

Use `bin/overlay-info --overlay-path /path/to/checkout --json` to inspect current
inventory, policy, configuration and Git identity without a persistent cache.
Read [the context guide](docs/overlay-info.md) for summary truncation, unchecked
Git state and the opt-in `--full` configuration/mask dump. Provision the tools
environment first; inspection does not sync dependencies. This output is context,
not a QA or build verdict.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits |
