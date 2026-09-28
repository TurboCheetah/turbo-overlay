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

### Bump Package Version

```bash
.agents/skills/overlay-tools/bin/update-ebuild -v 1.2.3 category/package

# With PR creation
.agents/skills/overlay-tools/bin/update-ebuild --pr -v 1.2.3 category/package

# With MY_PV and upstream URL
.agents/skills/overlay-tools/bin/update-ebuild --pr -v 1.2.3 -m "1.2.3" --upstream-url "https://..." category/package
```

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
stage3 and Portage snapshot (`--pull --no-cache`); subsequent runs reuse it.
The image intentionally tracks `latest`, so results can shift between rebuilds.
The checkout is mounted read-only. Portage fetches the distfile, validates the
Manifest, and runs `unpack`, `prepare`, `configure`, `compile`, and `install` into
a disposable container image directory. Repeat `--expect RELATIVE/PATH` to
assert installed files exist. Exit nonzero on any phase or assertion failure.
It does **not** install runtime dependencies, run the GUI, or prove the package
works on a real desktop; missing-library QA notices from a bare stage3 need
confirmation after a full dependency install. Only test trusted ebuilds:
Docker is not a sandbox for hostile package code.

## Development

```bash
cd .agents/skills/overlay-tools

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
