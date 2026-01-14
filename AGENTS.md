# AGENTS.md - turbo-overlay Development Guide

This file contains essential information for agentic coding agents working in this Gentoo overlay.

---

## Project Overview

**turbo-overlay** - Gentoo overlay (package definitions for Portage)
- Architecture: amd64
- EAPI: 8 (all ebuilds)
- Packages: 15 ebuilds across 5 categories
- Master repository: gentoo

Categories: games-util (vkbasalt), media-video (hayase-bin, lossless-cut), net-im (goofcord, vesktop-bin), x11-terms (warp-bin)

---

## Build/Test/Lint Commands

```bash
# QA validation (entire overlay)
pkgcheck scan .

# QA validation (specific package)
pkgcheck scan category/package

# Update Manifest checksums
ebuild category/package-name-version.ebuild manifest

# Build and install (Gentoo system)
emerge --ask --verbose --oneshot category/package

# Run tests (if src_test defined)
FEATURES="test" ebuild path/to/ebuild test

# Step-by-phase build
ebuild path/to/ebuild clean setup unpack prepare configure compile install
```

---

## Overlay Maintenance Tools

Located at `.opencode/skill/overlay-tools/`. Requires [uv](https://github.com/astral-sh/uv).

### Check for Updates

```bash
# Check all packages for upstream updates
.opencode/skill/overlay-tools/bin/check-updates

# Check specific package
.opencode/skill/overlay-tools/bin/check-updates -p net-im/goofcord

# JSON output (for scripting)
.opencode/skill/overlay-tools/bin/check-updates --json
```

### Bump Package Version

```bash
# Simple version bump
.opencode/skill/overlay-tools/bin/update-ebuild -v 1.2.3 category/package

# With MY_PV mapping (for packages like warp-bin)
.opencode/skill/overlay-tools/bin/update-ebuild -v 0.2025.12.10.08.12_p03 -m "0.2025.12.10.08.12.stable_03" x11-terms/warp-bin

# Dry run to preview
.opencode/skill/overlay-tools/bin/update-ebuild -n -v 2.0.0 net-im/goofcord

# Create PR automatically
.opencode/skill/overlay-tools/bin/update-ebuild --pr -v 3.68.0_pre -m "3.68.0" media-video/lossless-cut
```

### Automated Updates (GitHub Actions)

The workflow at `.github/workflows/check-updates.yml` runs weekly to check for updates and create PRs automatically.

---

## Code Style Guidelines

### Variable Declaration Order (Strict)
```bash
# Copyright header (required)
# Copyright 1999-202X Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit eclass1 eclass2 eclass3

MY_PV="custom-version"
DESCRIPTION="Short description (max 80 chars)"
HOMEPAGE="https://example.com"
SRC_URI="https://example.com/${P}.tar.gz"
S="${WORKDIR}"

LICENSE="license-name"
SLOT="0"
KEYWORDS="~amd64"
IUSE="+flag -otherflag"
RESTRICT="mirror bindist strip"
QA_PREBUILT="opt/${PN}/*"

RDEPEND="category/pkg-name"
BDEPEND="build-tool"
DEPEND="${RDEPEND}"
```

### Formatting Rules
- Indent with **ONE TAB** per level
- Use `${variable}` not `$variable`
- Conditions: `[[ ... ]]` not `[ ... ]`
- Line length: Keep under 80-100 chars

### Import/Inherit Patterns
```bash
# Binary packages (.deb, .pkg.tar.zst, AppImage)
inherit unpacker desktop xdg

# Source packages with meson
inherit meson multilib-minimal

# Live VCS packages
inherit git-r3

# Multilib support (32-bit + 64-bit)
MULTILIB_COMPAT=(abi_x86_{32,64})
```

---

## Binary Package Guidelines

### Common Eclasses
```bash
inherit unpacker desktop xdg
```

### Installation Pattern
```bash
src_install() {
    # Install to /opt
    insinto /opt/${PN}
    doins -r opt/${PN}/*
    fperms 0755 /opt/${PN}/${PN}

    # Symlink to PATH
    dosym ../opt/${PN}/${PN} /usr/bin/${PN}

    # Desktop integration
    domenu "${WORKDIR}/usr/share/applications/${PN}.desktop" || die "message"
    doicon -s 512 icon.png || die "message"
}

pkg_postinst() {
    xdg_desktop_database_update
    xdg_mimeinfo_database_update
    xdg_icon_cache_update
}

pkg_postrm() {
    xdg_desktop_database_update
    xdg_mimeinfo_database_update
    xdg_icon_cache_update
}
```

### QA Requirements (Mandatory)
```bash
RESTRICT="strip"
QA_PREBUILT="opt/${PN}/*"  # For /opt packages
QA_PREBUILT="usr/bin/${PN}-bin"  # For single binaries
```

### Archive Handling
- `.deb`: `inherit unpacker` + `unpacker_src_unpack`
- `.pkg.tar.zst`: `inherit unpacker` (needs `app-arch/zstd` in BDEPEND)
- AppImage: Direct install via `newbin`, depend on `sys-fs/fuse:0`

---

## AppImage Package Pattern

```bash
SRC_URI="https://github.com/mifi/lossless-cut/releases/download/v${PV}/LosslessCut-linux-x86_64.AppImage -> ${P}.AppImage"
S="${WORKDIR}"
RESTRICT="strip"
QA_PREBUILT="usr/bin/losslesscut-bin"

RDEPEND="
    sys-fs/fuse:0
    dev-libs/expat
    dev-libs/glib:2
    # ... standard Electron dependencies
"

src_install() {
    newbin "${DISTDIR}"/${P}.AppImage losslesscut-bin
    domenu "${FILESDIR}"/no.mifi.losslesscut.desktop
    doicon "${FILESDIR}"/no.mifi.losslesscut.svg
    insinto /usr/share/metainfo
    newins "${DISTDIR}"/${P}-metainfo.xml no.mifi.losslesscut.appdata.xml
}
```

**Key requirements**: `sys-fs/fuse:0`, `RESTRICT="strip"`, `QA_PREBUILT`

---

## Live Package (9999) Pattern

```bash
if [[ ${PV} == "9999" ]]; then
    EGIT_REPO_URI="https://github.com/user/repo.git"
    EGIT_SUBMODULES=()
    inherit git-r3
    SRC_URI=""
    KEYWORDS="-* ~amd64"
else
    SRC_URI="https://github.com/user/repo/archive/v${PV}.tar.gz -> ${P}.tar.gz"
    KEYWORDS="~amd64"
    S="${WORKDIR}/repo-${PV}"
fi

# For multilib live packages
MULTILIB_COMPAT=(abi_x86_{32,64})
inherit meson multilib-minimal git-r3
```

---

## Multilib Pattern

```bash
MULTILIB_COMPAT=(abi_x86_{32,64})

inherit meson multilib-minimal

RDEPEND="
    >media-libs/vulkan-loader-1.1:=[${MULTILIB_USEDEP},layers]
    x11-libs/libX11[${MULTILIB_USEDEP}]
"

multilib_src_configure() {
    meson_src_configure
}

multilib_src_compile() {
    meson_src_compile
}

multilib_src_install() {
    meson_src_install
}

multilib_src_install_all() {
    dodoc "${S}/config/example.conf"
}
```

---

## Dependencies and USE Flags

### Conditional Dependencies
```bash
RDEPEND="
    >=category/pkg-1.0
    useflag? ( category/optional-pkg )
    !conflictflag? ( !category/conflicting-pkg )
    reshade-shaders? ( dev-util/reshade-shaders )
"
```

### USE Flag Documentation
USE flags documented in `metadata.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE pkgmetadata SYSTEM "https://www.gentoo.org/dtd/metadata.dtd">
<pkgmetadata>
    <maintainer type="person">
        <email>dev@turbo.ooo</email>
        <name>turbo</name>
    </maintainer>
    <use>
        <flag name="reshade-shaders">Install <pkg>dev-util/reshade-shaders</pkg>.</flag>
    </use>
    <upstream>
        <remote-id type="github">DadSchoorse/vkBasalt</remote-id>
    </upstream>
</pkgmetadata>
```

---

## Helper Functions

### Installation Functions
```bash
dobin binary-name              # Install to /usr/bin, set executable
doins -r directory/          # Copy directory recursively
newbin source-filename target-name  # Install with rename
newins source-filename target-name  # Install file with rename
insinto /target/path          # Set destination for doins
dodoc file1 file2            # Install documentation
domenu desktop-file          # Install .desktop file
doicon -s 512 icon-file     # Install icon with size
```

### Links and Permissions
```bash
dosym /path/to/target /path/to/link    # Create symlink
dosym -r /opt/app/bin/app /usr/bin/app  # Relative symlink (EAPI 8)
fperms 0755 /path/to/file              # Set permissions
```

### Messaging
```bash
einfo "Information message"
ewarn "Warning message"
eerror "Error message"
die "Fatal error description"  # Exit with error
```

---

## Version Patterns

### Suffixes
- `_pre`: Pre-release (e.g., `1.2_pre`)
- `_p`: Patch/snapshot (e.g., `1.2_p03`)
- `_rc`: Release candidate (e.g., `1.0_rc1`)
- `9999`: Live/VCS version (git HEAD)
- `-rN`: Gentoo revision (ebuild-only changes, e.g., `1.0-r1`)
- `-bin`: Pre-compiled binaries

### Version Bumps vs Revisions
- Bump: `foo-1.1.ebuild` → `foo-1.2.ebuild` (upstream source changed)
- Revision: `foo-1.1.ebuild` → `foo-1.1-r1.ebuild` (ebuild-only changes)

---

## EAPI 8 Features

### New Features
- **IDEPEND**: Install-time dependencies (for `pkg_postinst` tools)
- **dosym -r**: Create relative symlinks automatically
- **Selective fetch/mirror**: `fetch+` and `mirror+` prefixes in `SRC_URI`
- **Bash 5.0**: Use Bash 5.0 features
- **Accumulated RESTRICT/PROPERTIES**: Now accumulated across eclasses

### Deprecated Features
- Use `use` not `useq`/`hasq`
- `PATCHES` cannot pass options (e.g., `-p0`)
- 7z/RAR not in native `unpack` (use `unpacker.eclass`)

---

## Git Workflow and Commit Messages

### Commit Message Format
```
category/package: add 1.2.3

Body (optional, 72-char wrap).

Signed-off-by: Name <email@example.com>
```

### Common Prefixes
- `add`: New version
- `drop`: Remove old version
- `version bump`: Update version
- `fix`: Bug fix
- `update metadata`: metadata.xml changes

### Example from this overlay
```
x11-terms/warp-bin: add 0.2025.12.10.08.12_p03, drop 0.2025.09.17.08.11_p02
```

---

## Overlay Metadata

### Required Files
- `metadata/layout.conf`: Defines `masters = gentoo`
- `profiles/repo_name`: Repository name (`turbo-overlay`)
- `Manifest`: File hashes (BLAKE2B and SHA512)
- `metadata.xml`: Per-package metadata (maintainer, USE flags, upstream)

### Repository Structure
```
turbo-overlay/
├── metadata/
│   ├── layout.conf
│   └── md5-cache/
├── profiles/
│   ├── repo_name
│   └── updates/
├── licenses/
│   └── OSL-3.0
└── categories/
    └── package-name/
        ├── package-name-version.ebuild
        ├── Manifest
        ├── files/
        └── metadata.xml
```

---

## Known Issues to Fix

### Missing metadata.xml
- **x11-terms/warp-bin**: No metadata.xml file
  - **Action**: Create metadata.xml with maintainer info

### Missing QA_PREBUILT
- **media-video/hayase-bin**: Binary package without `QA_PREBUILT`
- **net-im/goofcord**: Binary package without `QA_PREBUILT`
  - **Action**: Add `QA_PREBUILT="opt/${PN}/*"`

### Deprecated Packages
- **net-im/vesktop-bin**: Marked as DEPRECATED in pkg_setup
  - **Action**: This package should be removed entirely

---

## Quick Reference

### Adding New Binary Package
1. Create category directory if needed
2. Create package directory: `category/package-name/`
3. Write ebuild with EAPI 8 header
4. Add `metadata.xml` with maintainer info
5. Add `files/` subdirectory for assets
6. Run `ebuild path/to/ebuild manifest`
7. Test with `pkgcheck scan category/package-name`

### Common Commands
```bash
# Manifest update
ebuild path/to/ebuild manifest

# QA scan
pkgcheck scan .

# Install test
emerge -1v package-name

# View package info
emerge --info package-name
```

---

## External Documentation

- **Devmanual**: https://devmanual.gentoo.org/
- **Policy Guide**: https://projects.gentoo.org/qa/policy-guide/
- **EAPI 8 Spec**: https://projects.gentoo.org/pms/8/pms.html
- **Eclass Reference**: https://devmanual.gentoo.org/eclass-reference/
- **GLEP 66**: Commit message format
- **pkgcheck**: https://pkgcore.github.io/pkgcheck/
