---
name: check-updates
description: Scan turbo-overlay packages and check for available upstream updates. Use when asked "any updates?", "check for new versions", or for periodic overlay maintenance.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
---

# Check Updates Skill

Intelligently scans the turbo-overlay Gentoo repository to determine which packages have available upstream updates.

## Overview

The `check_updates.py` script at `.opencode/skill/check-updates/scripts/check_updates.py` scans all packages in the overlay, extracts upstream source information, and checks for newer versions using GitHub API or flags packages for manual review.

## When to Use

- User asks "are there any updates available?"
- User asks "check for new versions"
- Periodic maintenance checks (weekly/monthly)
- Before planning version bump work
- After upstream project announces new releases

## What It Does

1. **Discovers packages**: Scans overlay for all ebuilds (excludes deprecated/)
2. **Extracts metadata**: Parses SRC_URI, HOMEPAGE, metadata.xml for upstream sources
3. **Checks upstream**: Queries GitHub API for latest releases
4. **Reports findings**: Shows current vs. available versions with actionable recommendations

## Supported Sources

| Source Type | Check Method | Status |
|-------------|--------------|--------|
| GitHub Releases | Auto via API | Fully supported |
| Custom APIs (hayase.watch, warp.dev) | Manual | Flagged for review |
| Unknown sources | Manual | Flagged for investigation |

## Script Usage

```bash
check_updates.py [OPTIONS]
```

### Options

| Flag | Description |
|------|-------------|
| `--package CATEGORY/NAME` | Check specific package only |
| `--json` | Output JSON format (for scripting) |
| `--verbose` | Show detailed progress |
| `--overlay-path PATH` | Path to overlay (default: current directory) |

### Common Examples

```bash
# Check all packages
cd /home/turbo/.local/src/turbo-overlay
python .opencode/skill/check-updates/scripts/check_updates.py

# Check specific package
python .opencode/skill/check-updates/scripts/check_updates.py --package net-im/goofcord

# JSON output for scripting
python .opencode/skill/check-updates/scripts/check_updates.py --json

# With GitHub token for higher rate limits
GITHUB_TOKEN=ghp_xxx python .opencode/skill/check-updates/scripts/check_updates.py
```

## Output Format

```
turbo-overlay Update Check
==========================

✓ media-video/lossless-cut
  Current: 3.64.0
  Latest:  3.65.0 (GitHub: mifi/lossless-cut)
  Status:  UPDATE AVAILABLE
  Action:  Use update-ebuild skill to bump version

✓ net-im/goofcord
  Current: 1.6.0
  Latest:  1.6.0 (GitHub: Milkshiift/GoofCord)
  Status:  UP TO DATE

⚠ media-video/hayase-bin
  Current: 6.4.48
  Source:  https://hayase.watch/releases
  Status:  MANUAL CHECK REQUIRED
  Action:  Visit https://hayase.watch/releases

⚠ x11-terms/warp-bin
  Current: 0.2026.01.07.08.13_p01
  Source:  https://www.warp.dev/changelog
  Status:  MANUAL CHECK REQUIRED
  Action:  Visit https://www.warp.dev/changelog

Summary:
- 2 packages checked automatically
- 1 update available
- 2 require manual check
- 0 errors
```

## Decision Tree

```
Package Found
├─ In deprecated/ ? → SKIP
├─ Has metadata.xml remote-id type="github" ? → GitHub API Check
├─ SRC_URI contains github.com ? → GitHub API Check (extract org/repo)
├─ SRC_URI contains known vendor domain ? → Flag MANUAL CHECK
└─ Otherwise → Flag UNKNOWN SOURCE
```

## Integration with update-ebuild

After identifying updates, use the update-ebuild skill to bump versions:

```bash
# Example workflow
# 1. Check for updates
python .opencode/skill/check-updates/scripts/check_updates.py

# 2. Found: lossless-cut 3.64.0 → 3.65.0
# 3. Bump version using update-ebuild skill
update-ebuild -v 3.65.0_pre -m "3.65.0" media-video/lossless-cut
```

## Caching & Rate Limits

- GitHub API: 60 requests/hour (unauthenticated), 5000/hour (with `GITHUB_TOKEN`)
- Cache results in `.opencode/skill/check-updates/.cache/` (30-minute TTL)
- Respects `X-RateLimit-*` headers
- Fails gracefully when rate-limited

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Updates available |
| 1 | Errors occurred |
| 2 | All packages up-to-date |

## Limitations

- Cannot auto-check custom vendor APIs without specific implementation
- GitHub API rate limits may require authentication for large overlays
- Version comparison may fail for complex version schemes (handled gracefully)
- Does not check for security advisories (separate concern)

## Dependencies

- Python 3.9+
- `requests` library
- `packaging` library (for version comparison)

Install with:
```bash
pip install requests packaging
```

## Related Files

- Script: `.opencode/skill/check-updates/scripts/check_updates.py`
- Cache: `.opencode/skill/check-updates/.cache/` (auto-created, gitignored)
- update-ebuild skill: `.opencode/skill/update-ebuild/SKILL.md`
