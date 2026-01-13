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
.opencode/skill/check-updates/scripts/check-updates
```

### Bump Package Version

```bash
.opencode/skill/update-ebuild/scripts/update-ebuild -v 1.2.3 category/package

# With PR creation
.opencode/skill/update-ebuild/scripts/update-ebuild --pr -v 1.2.3 category/package

# With MY_PV and upstream URL
.opencode/skill/update-ebuild/scripts/update-ebuild --pr -v 1.2.3 -m "1.2.3" --upstream-url "https://..." category/package
```

## Development

```bash
cd .opencode/skill/overlay-tools

# Run tests
uv run pytest

# Run with dev dependencies
uv run --extra dev pytest -v

# Type check (if basedpyright is installed)
uv run basedpyright src/
```

## Architecture

```
overlay-tools/
├── src/overlay_tools/
│   ├── cli/
│   │   ├── check_updates.py    # check-updates CLI
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
│       └── versions.py         # Version handling
└── tests/
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits |
