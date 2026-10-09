# Create a binary ebuild starter

`bin/create-ebuild` renders a new package's ebuild and `metadata.xml`. Preview
is the default. It writes only when you pass the exact `--write` option.
This is a starter for maintainer review, not a complete installable package.

Prepare the existing tool environment separately:

```bash
cd .agents/skills/overlay-tools
uv sync --locked --group dev
```

The launcher uses that environment's Python directly in isolated mode. It ignores
caller-directory imports, `PYTHONPATH`, and the user site, so an overlay checkout
cannot shadow the creator module or load its own `sitecustomize.py`. It inserts
its own checkout's `src` path first and checks the package origin before import,
even if the environment's editable install points to another checkout. The
prepared interpreter and its installed startup code must still be trusted.
The launcher does not run `uv sync`, resolve dependencies, download Python, or
access the network.
A missing environment exits `127` with a setup command.

## Preview and write

Pass an existing overlay root explicitly, even when running from that root:

```bash
.agents/skills/overlay-tools/bin/create-ebuild dev-util/example-bin \
  --overlay-path /path/to/overlay \
  --version 1.2.3 \
  --template binary-direct \
  --upstream-url https://downloads.example.org/example-1.2.3.tar.gz \
  --license MIT \
  --description 'Example binary tool' \
  --homepage https://example.org/ \
  --maintainer-email owner@example.org
```

Preview prints the exact proposed paths, complete ebuild and XML, missing
artifacts, and follow-up commands. It creates no directories or files.
`--dry-run` makes the default explicit. Add `--write` to the same command to
create the package. `--write` and `--dry-run` are mutually exclusive.
There is no overwrite flag. Any existing package target, including an empty
directory, file, dangling symlink, Manifest or metadata, causes refusal in
both modes.

Required arguments are the exact unversioned `category/package`, `--overlay-path`,
`--version`, `--template`, `--upstream-url`, `--license`, `--description`,
`--homepage`, and `--maintainer-email`.

Optional arguments:

| Option | Behavior |
| --- | --- |
| `--maintainer-name NAME` | Add an XML name with escaping. Omit to write only the supplied email. |
| `--binary-name NAME` | Name of the assumed source binary and installed command. Defaults to the package name without a trailing `-bin`. |
| `--keywords 'TOKENS'` | Space-separated architecture tokens. Defaults to `~amd64`; pass `--keywords ''` to leave keywording for review. Verify the artifact's architecture before applying. |
| `--eapi 8` | EAPI 8 is the only supported EAPI. |

No maintainer, license, URL or version comes from Git config, environment identity,
an existing package, or host metadata. The `~amd64` starter default is a template
choice, not detected architecture or proof of compatibility.

## Templates

- `binary-direct` uses Portage's default archive unpacking for URL paths ending
  in `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, or `.zip`, even with a query string.
  When the path has no supported suffix, it checks decoded `file` and `filename`
  query values, including when other parameters follow them. Other query keys,
  such as `token`, do not select unpacking. Conflicting filename hints cause
  refusal. A supported path suffix takes precedence over query hints. The
  original URL is always preserved byte-for-byte; decoding is only for hints.
  These are filename heuristics, not verified content types. Check the artifact
  yourself before using the starter. An archive gets `${P}` plus its suffix.
  The initial `S="${WORKDIR}"` assumes a binary at `${WORKDIR}/NAME`; it does not
  infer extracted directories. Set `S` and the binary path to the actual upstream
  layout. `${WORKDIR}/${P}` is not universal, particularly for `-bin` package
  names that differ from the upstream directory. Without a supported hint, the
  download is treated as a single binary, renamed `${P}.bin`, with unpacking
  disabled. Do not select this template for an unsupported archive.
- `binary-deb` inherits `unpacker`, calls `unpacker_src_unpack`, and assumes
  a standalone `${WORKDIR}/usr/bin/NAME`. It does not copy the whole Debian
  filesystem or claim to supply Electron/browser packaging. Review `/opt`
  layouts, launcher scripts, shared libraries, sandbox permissions and desktop
  integration before using it.
- `binary-appimage-intact` installs `${DISTDIR}/${P}.AppImage` directly with
  `newbin`. It never executes a vendor extractor. It declares `sys-fs/fuse:0`,
  but that is not a complete runtime dependency list.

All templates disable configure/compile work, use `RESTRICT="mirror strip"`,
and declare the installed binary's `QA_PREBUILT` path. They produce no desktop
files, icons, metainfo, license texts, Manifest or metadata cache. Review
redistribution rights before changing mirror restrictions.

## Validation and writes

This initial interface intentionally accepts a conservative subset:

- Category and package are literal filename components, not paths, versioned
  atoms, dependency operators or slots. Repository housekeeping categories
  are reserved. No spelling or version is normalized.
- Versions use Gentoo numeric components, an optional letter, `_alpha`, `_beta`,
  `_pre`, `_rc`, `_p` suffixes, and an optional `-rN` revision.
- Licenses are one or more space-separated identifiers. License groups,
  conditional expressions and `||` are not supported. This checks syntax,
  not availability in the overlay/master or permission to redistribute.
- URLs are literal canonical HTTPS URLs with lowercase DNS-style hosts. No
  credentials, port, fragment, Bash expansion, shell quotes, whitespace,
  control characters or dot path segments are accepted. Percent escapes must
  decode to the same restricted character set. Use an immutable, release-specific
  URL, not `${PV}` interpolation or a mutable latest-release endpoint. The tool
  validates URL syntax only; neither a version string in the URL nor a
  content-addressed-looking path proves immutability. Maintainers must review
  upstream's release and retention policy. The offline creator cannot establish
  whether the URL will keep serving the same bytes.
  Literal `@@` sequences in URLs are preserved as data, never template syntax.
- Descriptions are nonempty printable single-line text of at most 80 characters.
  Names are at most 120 characters. Bash substitutions, backslashes, semicolons,
  pipes and template markers are rejected. Apostrophes are Bash-quoted; XML text
  is escaped.
- Binary names are single non-option filename components, not relative paths.
  Keyword tokens are syntactic architecture names with optional `~`; the tool
  does not verify architecture support.
- The root must already contain a regular `profiles/repo_name` with a valid
  repository name and a regular `metadata/layout.conf`. The root, ancestors,
  category, package target and marker files must not be symlinks. A nonexistent
  root or package subdirectory passed as the root fails closed.

All input, target paths, template files and unresolved placeholders are checked
before creating any directories. Unknown or malformed markers in the original
templates, including dangling `@@` prefixes, cause refusal. Substitution runs
once over the original template and never scans inserted values for markers.
Writes use directory file descriptors and
no-follow opens. Files are created exclusively in a randomly named private
staging directory. Linux `renameat2` with `RENAME_NOREPLACE` publishes the whole
package at once. An existing or concurrently created target is never replaced,
even an empty directory. Linux/libc/filesystem support for that operation is
required; failure does not fall back to an overwriting rename.

On an I/O or verification failure, cleanup unlinks only recorded file identities
and removes empty owned directories without recursive traversal. This includes
failures in the final checks after publication. A clean rollback permits retry.
Unknown or replaced entries are left alone. If the identity read immediately after
creating a category or staging directory fails, the tool leaves that unidentified
entry untouched rather than guessing ownership or retrying the read to delete it.
Incomplete cleanup reports the affected absolute paths and any residual artifacts
it can inspect. Moved or replaced categories are preserved and reported even when
their original directory is now empty. Descriptor-close errors do not stop the
remaining cleanup or replace the original failure. This also applies to overlay
validation, marker-file ownership transfers, every root-traversal parent, and
temporary roots opened for attachment checks. A failed parent close still closes
the newly opened child once. Diagnostics name the affected absolute file or
directory path. The tool reports the uncertain descriptor state and never retries
a close, because the descriptor number may already belong to another open file.
A close error before publication prevents
publication and triggers rollback. If final descriptor cleanup fails after all
publication checks passed, the complete published starter is retained, its path
is reported, and the command exits `1` without printing a successful write report.
Review reported paths manually before retrying.
Use a trusted checkout whose access permissions exclude hostile writers. These
checks do not make simultaneous mutations by a malicious process with the same
Unix identity a security boundary. Process termination or power loss may leave
an unpublished `.create-ebuild-*` directory to inspect manually.

Exit codes are `0` for a successful preview or write, `1` for validation or I/O
refusal, `2` for argument errors, and `127` for an unprepared launcher environment.

## Finish the package manually

Before executing any ebuild phase, confirm an immutable release-specific source
with upstream and review the artifact type, extracted layout, command name,
licensing, bundled components and dependencies. Set `S` and install paths to
the actual upstream layout, not an assumed package-name directory. Add any
required assets. Then, from the overlay root, adapt the printed commands:

```bash
ebuild dev-util/example-bin/example-bin-1.2.3.ebuild manifest
pkgcheck scan -f latest dev-util/example-bin
.agents/skills/overlay-tools/bin/test-ebuild \
  --overlay-path /path/to/overlay \
  --expect usr/bin/example \
  dev-util/example-bin/example-bin-1.2.3.ebuild
```

The first command can fetch upstream data and executes an ebuild. The phase
checker runs trusted code in Docker and needs a prepared image, or an explicit
`--build`. Run these only after review, not merely because generation succeeded.
A phase test does not establish complete runtime dependencies or prove that an
application works. `create-ebuild` itself never downloads, creates a Manifest,
updates cache, executes ebuilds, runs a vendor binary, or installs anything on
the host.
