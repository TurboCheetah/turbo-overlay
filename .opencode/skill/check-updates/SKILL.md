---
name: check-updates
description: Scan turbo-overlay packages and check for available upstream updates. Use when asked "any updates?", "check for new versions", or for periodic overlay maintenance.
license: MIT
metadata:
  audience: maintainers
  workflow: gentoo-overlay
---

# Check Updates Skill

Scans the turbo-overlay to find packages with available upstream updates.

## Quick Start

```bash
# Check all packages
.opencode/skill/check-updates/scripts/check-updates

# Check specific package
.opencode/skill/check-updates/scripts/check-updates -p net-im/goofcord

# JSON output for scripting
.opencode/skill/check-updates/scripts/check-updates --json
```

## Requirements

- **uv** - The script will prompt you to install it if missing

## Options

| Flag | Description |
|------|-------------|
| `-p, --package CATEGORY/NAME` | Check specific package only |
| `--json` | Output JSON format (for scripting) |
| `-v, --verbose` | Show detailed progress |
| `--overlay-path PATH` | Path to overlay (default: current directory) |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits (5000/hr vs 60/hr) |

## Output

The tool displays a rich table showing:
- 🚀 Update available
- ✓ Up to date
- 👀 Manual check required (non-GitHub sources)
- ✗ Error

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Updates available |
| 1 | Errors occurred |
| 2 | All packages up-to-date |

## Workflow

1. Run check-updates to find outdated packages
2. Use `/update-ebuild` skill to bump versions
3. Test with `emerge -1v category/package`
4. Push changes

## Architecture

This skill uses the shared `overlay-tools` Python library located at `.opencode/skill/overlay-tools/`. The library provides:

- Version parsing and comparison
- Ebuild file parsing
- GitHub API integration with caching
- Rich terminal output

## Related

- `/update-ebuild` - Bump package versions
