---
name: update-ebuild
description: Use when bumping ebuild versions, updating Gentoo packages, or when the user mentions "version bump", "new release", "update package", or needs to modify ebuild versions in this overlay.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
---

# Update Ebuild Skill

This skill provides guidance for updating Gentoo ebuilds to new versions in the turbo-overlay.

## Overview

The `update-ebuild` script at `.opencode/skill/update-ebuild/scripts/update-ebuild` automates version bumps for Gentoo ebuilds. It handles version normalization, Manifest updates, and git commits.

## When to Use

- Bumping a package to a new upstream version
- Creating a new ebuild from the latest existing one
- Updating packages with complex versioning (like warp-bin)

## Script Usage

```bash
update-ebuild [OPTIONS] -v VERSION path/to/package
```

### Options

| Flag | Description |
|------|-------------|
| `-v VERSION` | New version (required). Gentoo format: `1.2.3`, `1.2.3_p1`, `1.2.3_alpha1` |
| `-m MY_PV` | Set MY_PV for upstream version mapping (critical for warp-bin) |
| `-n` | Dry run - preview changes without applying |
| `-s` | Skip git operations |
| `-l` | Lenient mode - allow non-standard version formats |
| `-k` | Keep old ebuild (don't remove oldest version) |
| `-h` | Show help |

### Common Examples

```bash
# Simple version bump
update-ebuild -v 1.2.3 media-video/hayase-bin

# Complex version with MY_PV (warp-bin style)
update-ebuild -v 0.2025.12.10.08.12_p03 -m "0.2025.12.10.08.12.stable_03" x11-terms/warp-bin

# Dry run to preview
update-ebuild -n -v 2.0.0 net-im/goofcord

# Keep multiple versions
update-ebuild -k -v 1.3.0 games-util/vkbasalt
```

## Package-Specific Patterns

### Simple Binary Packages (hayase-bin, goofcord)

These use `${PV}` directly in SRC_URI:

```bash
# Just bump the version
update-ebuild -v 6.5.0 media-video/hayase-bin
```

### Complex Version Mapping (warp-bin)

warp-bin uses `MY_PV` because upstream versions like `0.2025.10.08.08.12.stable_03` don't match Gentoo's version format:

```bash
# The ebuild filename uses Gentoo-normalized version
# MY_PV holds the actual upstream version for SRC_URI
update-ebuild -v 0.2025.12.15.08.12_p04 -m "0.2025.12.15.08.12.stable_04" x11-terms/warp-bin
```

**Version mapping rule for warp-bin:**
- Upstream: `X.YYYY.MM.DD.HH.MM.stable_NN`
- Gentoo: `X.YYYY.MM.DD.HH.MM_pNN`

### AppImage Packages (lossless-cut)

These often use `MY_PV` with `_pre` suffix:

```bash
update-ebuild -v 3.66.0_pre -m "3.66.0" media-video/lossless-cut
```

### Source Packages (vkbasalt)

Standard version bumps, may want to keep multiple versions:

```bash
update-ebuild -v 0.3.3.0 games-util/vkbasalt
```

## Manual Steps After Update

1. **Verify the ebuild** - Check that SRC_URI resolves correctly
2. **Test installation** - `emerge -1v category/package`
3. **Run QA checks** - `pkgcheck scan category/package`
4. **Push changes** - After verification

## Version Format Reference

| Gentoo Format | Meaning |
|---------------|---------|
| `1.2.3` | Standard release |
| `1.2.3a` | Minor patch (letter suffix) |
| `1.2.3_p1` | Patch release |
| `1.2.3_alpha1` | Alpha release |
| `1.2.3_beta2` | Beta release |
| `1.2.3_pre` | Pre-release |
| `1.2.3_rc1` | Release candidate |
| `1.2.3-r1` | Ebuild revision (no upstream change) |
| `9999` | Live/git version |

## Troubleshooting

### "Invalid version format"
Use `-l` flag for lenient mode, or convert the version to Gentoo format.

### Manifest update fails
Ensure you have proper permissions. The script uses `doas` or `sudo` automatically.

### MY_PV not updated
The script only updates MY_PV if it exists in the ebuild. For packages that need MY_PV but don't have it, you'll need to add it manually first.

## Related Files

- Script: `.opencode/skill/update-ebuild/scripts/update-ebuild`
- AGENTS.md: Contains ebuild coding standards
