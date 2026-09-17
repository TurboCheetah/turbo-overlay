---
name: overlay-tools
description: Gentoo overlay maintenance tools. Use /check-updates to find outdated packages, /update-ebuild to bump versions.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
aliases:
  - check-updates
  - update-ebuild
---

# Overlay Tools

Maintenance tools for turbo-overlay Gentoo packages.

## Commands

### check-updates

Scan packages for available upstream updates.

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

## Requirements

- **uv** - Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits |

## Workflow

1. Run `check-updates` to find outdated packages
2. Run `update-ebuild --pr` to bump version and create PR
3. Or manually: `update-ebuild -v X.Y.Z category/package`
4. Test: `emerge -1v category/package`
5. QA: `pkgcheck scan category/package`

## Version Format Reference

| Gentoo Format | Meaning |
|---------------|---------|
| `1.2.3` | Standard release |
| `1.2.3_p1` | Patch release |
| `1.2.3_pre` | Pre-release |
| `1.2.3_alpha1` | Alpha |
| `1.2.3_beta2` | Beta |
| `1.2.3_rc1` | Release candidate |
