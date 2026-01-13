---
name: update-ebuild
description: Use when bumping ebuild versions, updating Gentoo packages, or when the user mentions "version bump", "new release", "update package", or needs to modify ebuild versions in this overlay.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
---

# Update Ebuild Skill

Automates version bumps for Gentoo ebuilds in the turbo-overlay.

## Quick Start

```bash
# Simple version bump
.opencode/skill/update-ebuild/scripts/update-ebuild-new -v 1.2.3 media-video/hayase-bin

# With MY_PV mapping (for warp-bin style packages)
.opencode/skill/update-ebuild/scripts/update-ebuild-new -v 0.2025.12.10.08.12_p03 -m "0.2025.12.10.08.12.stable_03" x11-terms/warp-bin

# Dry run to preview
.opencode/skill/update-ebuild/scripts/update-ebuild-new -n -v 2.0.0 net-im/goofcord
```

## Requirements

- **uv** - The script will prompt you to install it if missing

## Options

| Flag | Description |
|------|-------------|
| `-v, --version VERSION` | New version (required). Gentoo format: `1.2.3`, `1.2.3_p1`, `1.2.3_pre` |
| `-m, --my-pv MY_PV` | Set MY_PV for upstream version mapping |
| `-n, --dry-run` | Preview changes without applying |
| `-s, --skip-git` | Skip git operations |
| `-l, --lenient` | Allow non-standard version formats |
| `-k, --keep-old` | Keep old ebuild (don't remove oldest version) |

## Package-Specific Patterns

### Simple Binary Packages (hayase-bin, goofcord)

```bash
.opencode/skill/update-ebuild/scripts/update-ebuild-new -v 6.5.0 media-video/hayase-bin
```

### Complex Version Mapping (warp-bin)

warp-bin uses `MY_PV` because upstream versions don't match Gentoo format:

```bash
# Upstream: 0.2025.12.15.08.12.stable_04
# Gentoo:   0.2025.12.15.08.12_p04
.opencode/skill/update-ebuild/scripts/update-ebuild-new \
  -v 0.2025.12.15.08.12_p04 \
  -m "0.2025.12.15.08.12.stable_04" \
  x11-terms/warp-bin
```

### AppImage Packages (lossless-cut)

```bash
.opencode/skill/update-ebuild/scripts/update-ebuild-new -v 3.66.0_pre -m "3.66.0" media-video/lossless-cut
```

## What It Does

1. Copies the latest ebuild to new version filename
2. Updates MY_PV if provided
3. Removes oldest ebuild (unless `-k`)
4. Runs `ebuild ... manifest` to update checksums
5. Optionally commits changes to git

## Version Format Reference

| Gentoo Format | Meaning |
|---------------|---------|
| `1.2.3` | Standard release |
| `1.2.3_p1` | Patch release |
| `1.2.3_pre` | Pre-release |
| `1.2.3_alpha1` | Alpha release |
| `1.2.3_beta2` | Beta release |
| `1.2.3_rc1` | Release candidate |
| `1.2.3-r1` | Ebuild revision |

## Manual Steps After Update

1. Verify the ebuild - Check that SRC_URI resolves
2. Test installation - `emerge -1v category/package`
3. Run QA checks - `pkgcheck scan category/package`
4. Push changes

## Architecture

This skill uses the shared `overlay-tools` Python library located at `.opencode/skill/overlay-tools/`. The library provides:

- Version normalization and validation
- Ebuild file manipulation
- Git integration
- Rich terminal output

## Related

- `/check-updates` - Find outdated packages
