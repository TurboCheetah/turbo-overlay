---
name: overlay-tools
description: Gentoo overlay maintenance tools. Use /check-updates to find outdated packages, /update-ebuild to bump versions, /test-ebuild to run an ebuild's Portage phases in Docker.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
aliases:
  - check-updates
  - update-ebuild
  - test-ebuild
---

# Overlay Tools

Maintenance tools for turbo-overlay Gentoo packages.

## Commands

### check-updates

Scan packages for available upstream updates.

Read `metadata/update-exclusions.json` as the repository's explicit maintenance
policy. Both retired T3 desktop atoms and `dev-util/t3code-openrc` are excluded.
The OpenRC adapter is locally versioned, not bumped from upstream runtime
releases. `check-updates` omits excluded packages before channel selection or
upstream lookups and prints each atom and reason on stderr, even with `--json`.
This applies to full scans and explicit `--package` targets. A scan with no
eligible updates exits `2`. Daily and weekly workflow schedules remain unchanged.

Policy keys must be exact unversioned `category/package` atoms and values must
be non-empty reason strings. Missing policy means no exclusions. Malformed
JSON, types, atoms, duplicate keys, or reasons fail closed with exit `1` before
network access or writes. Do not infer exclusions from `profiles/package.mask`;
Portage selection and maintenance automation are separate policies.

```bash
# Check all packages
.agents/skills/overlay-tools/bin/check-updates

# Check specific package
.agents/skills/overlay-tools/bin/check-updates -p net-im/goofcord

# JSON output for scripting
.agents/skills/overlay-tools/bin/check-updates --json

# Only nightly-channel packages (the daily CI run)
.agents/skills/overlay-tools/bin/check-updates --channel nightly

# Everything except nightly-channel packages (the weekly CI run)
.agents/skills/overlay-tools/bin/check-updates --exclude-channel nightly
```

If a package is reported as `manual-check`, see
`.agents/skills/overlay-tools/docs/manual-update-checks.md`.

**Options:**

| Flag | Description |
|------|-------------|
| `-p, --package CATEGORY/NAME` | Check specific package only |
| `--channel CHANNEL` | Only check packages whose selected channel matches (repeatable) |
| `--exclude-channel CHANNEL` | Skip packages whose selected channel matches (repeatable) |
| `--json` | Output JSON format |
| `-v, --verbose` | Show detailed progress |
| `--overlay-path PATH` | Path to overlay (default: current directory) |

`--channel` and `--exclude-channel` are mutually exclusive. Both filter on the
channel a package *would be checked on* — derived from the `MY_PV` of its
highest-versioned ebuild: `stable`, `preview`, `dev`, `nightly`, or none.
Packages with no channel marker (no `MY_PV` channel suffix) have channel none,
so `--channel X` drops them and `--exclude-channel X` keeps them. A filter that
matches no package warns on stderr and exits `2`.

**Exit Codes:** `0` = updates available, `1` = errors, `2` = all up-to-date

### update-ebuild

Bump ebuild versions with optional PR automation.

`update-ebuild` refuses policy-excluded packages with exit `1` and their reason
before any writes, fetches, branching, commits, or pushes. `--dry-run`, `--pr`,
`--yes`, and `--skip-git` do not override the policy. See
[`README.md`](README.md#repository-update-policy) for the schema and
[the overlay migration guide](../../../README.md#t3-code-desktop-and-openrc)
for desktop migration and adapter ownership. Retain the deprecated desktop
ebuilds and Manifests; do not automatically uninstall existing packages.

```bash
# Version bump
.agents/skills/overlay-tools/bin/update-ebuild -y -v 1.2.3 media-video/hayase-bin

# With MY_PV mapping
.agents/skills/overlay-tools/bin/update-ebuild -y -v 0.2025.12.10.08.12_p03 -m "0.2025.12.10.08.12.stable_03" x11-terms/warp-bin

# Dry run
.agents/skills/overlay-tools/bin/update-ebuild -n -v 2.0.0 net-im/goofcord

# Create PR automatically (--pr implies -y)
.agents/skills/overlay-tools/bin/update-ebuild --pr -v 3.68.0_pre -m "3.68.0" media-video/lossless-cut
```

**Options:**

| Flag | Description |
|------|-------------|
| `-v, --version VERSION` | New version (required) |
| `-m, --my-pv MY_PV` | Set MY_PV for upstream version mapping |
| `-n, --dry-run` | Preview changes without applying |
| `-s, --skip-git` | Skip git operations |
| `-l, --lenient` | Allow non-standard version formats |
| `-k, --keep-old` | Keep old ebuild |
| `--skip-manifest` | Skip Manifest update (for CI) |
| `-y, --yes` | Auto-commit without prompting |
| `--pr` | Create PR after committing (implies -y) |
| `--base BRANCH` | Base branch for PR |
| `--branch BRANCH` | Override feature branch name |
| `--draft` | Create PR as draft |
| `--upstream-url URL` | Upstream release URL for PR body |

### test-ebuild

Run a specific version through real Portage phases in a disposable Gentoo
container. Build dependencies are emerged inside the container first. This is
not a host `emerge` and does not test runtime dependencies.

```bash
.agents/skills/overlay-tools/bin/test-ebuild --build \
  --overlay-path /path/to/pr-checkout \
  --expect opt/t3code-nightly-bin/t3code \
  --expect usr/bin/t3code \
  dev-util/t3code-nightly-bin/t3code-nightly-bin-0.0.43_pre202609272344.ebuild
```

**Options:**

| Flag | Description |
|------|-------------|
| `EBUILD` | Exact `category/package/package-version.ebuild` path (required) |
| `--overlay-path PATH` | Overlay checkout to mount read-only (default: this repo) |
| `--build` | Rebuild the image with a fresh stage3 and Portage snapshot |
| `--expect STAGED_PATH` | Require a relative path in the install image (repeatable) |

Build once (`--build`); subsequent calls reuse the local Docker image and exit 2
if it is missing. `docker/run-ebuild` is mounted from the checkout on each run,
so script changes do not need a rebuild. Use an
explicit PR checkout path rather than whichever branch happens to be current.
The overlay is bind-mounted read-only; Portage's distfiles, workdir and image
are ephemeral. An exit code of zero means fetch/Manifest validation and
unpack→install phases succeeded and the requested staged paths exist. It does
**not** prove that RDEPEND is complete, the package is installed, or its GUI
runs. A bare stage3 may report unresolved sonames for dependencies that would
be supplied by a real `emerge`. Build phases run without Portage's network
sandbox, so network access during `src_compile` is not caught. Only run trusted
ebuilds: Docker does not make untrusted build scripts safe.

**Exit Codes:** `0` = phases and assertions passed, `1` = build dependencies
failed to install, or a phase or assertion failed, `2` = invalid arguments or
overlay, missing Docker, image not built or failed to build, or container setup
failure (repos.conf, overlay registration), `127` = `uv` is not installed. Other
non-zero codes come from `docker run` itself (e.g. `137` if the container is
killed).

### Review new PRs

The [PR autopilot](autopilot/README.md) holds the webhook filter, stable-state
monitor, fail-closed merge gate, and tests. Its review policy is in
[`turbo-overlay-pr-autopilot`](../turbo-overlay-pr-autopilot/SKILL.md).
It does not process PRs created before its local activation baseline.
Secrets, webhook registration, cron schedule, and state stay outside Git.

## Requirements

- **uv** - Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Docker** (`test-ebuild` only) - https://docs.docker.com/engine/install/

## Checkout context

Before relying on package inventory or checkout identity, request
`bin/overlay-info --overlay-path /path/to/checkout --json`. Read
[the context guide](docs/overlay-info.md) for bounded lists, truncation counts,
unchecked Git state and opt-in `--full` masks/configuration. The command reads
current files without a persistent cache and does not execute verification
commands. Provision its tools environment separately.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits |

## Workflow

1. Run `check-updates` to find outdated packages
2. Run `update-ebuild --pr` to bump version and create PR
3. Or manually: `update-ebuild -v X.Y.Z category/package`
4. Phase-test the exact ebuild: `test-ebuild --overlay-path /path/to/pr-checkout category/package/package-version.ebuild` (add `--build` on first use)
5. Test: `emerge -1v category/package`
6. QA: `pkgcheck scan category/package`

## Version Format Reference

| Gentoo Format | Meaning |
|---------------|---------|
| `1.2.3` | Standard release |
| `1.2.3_p1` | Patch release |
| `1.2.3_pre` | Pre-release |
| `1.2.3_alpha1` | Alpha |
| `1.2.3_beta2` | Beta |
| `1.2.3_rc1` | Release candidate |
