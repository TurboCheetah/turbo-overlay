# Local read-only QA with qa-ebuild

`bin/qa-ebuild` checks local package files without installing dependencies,
regenerating metadata or Manifests, sourcing ebuilds, or fetching distfiles.
The launchers require a trusted `python3` 3.11+ on `PATH`, `/usr/bin/env` with
`-S` support, and the provisioned overlay-tools `.venv`. Bash is required only
for syntax checks. They do not run uv or resolve/install dependencies. Initialize
explicitly once with `uv sync --locked --group dev` in the tools directory.
A missing environment exits 2. Python 3.11+ is required by the shared package,
whose existing `core/__init__.py` imports `packaging` even though QA itself uses
only the standard library. Both QA launchers start Python in isolated mode and
disable bytecode writes. They ignore the working directory, inherited
`PYTHONPATH`, Python environment settings and user site-packages during startup.
After startup, they put their own adjacent `src` directory first on the import
path, regardless of the provisioned environment's editable-install origin.
Paths and arguments are passed as argv, not interpolated into Python code.
The executable shebang starts a standard-library Python bootstrap with `-I -S -B`
before any imports, then explicitly re-execs the provisioned Python with `-I -B`.
The host bootstrap skips all site initialization, including host `sitecustomize`
and `.pth` hooks. Neither launcher starts Bash, so inherited `BASH_ENV` or `ENV`
cannot run shell startup code before isolation. The subordinate syntax check
also strips Bash startup variables. Invoke the executables directly, not with
`bash bin/qa-ebuild` or an unisolated `python bin/qa-ebuild`.
The provisioned virtual environment and its site hooks, launcher/source files,
host Python, and external executables selected through `PATH` must still be trusted.
This is startup/import-path isolation, not a sandbox for malicious interpreters,
virtual environments, executable replacements or native loader settings.
Run either launcher from any working directory with an explicit overlay path.

```bash
.agents/skills/overlay-tools/bin/qa-ebuild --overlay-path . dev-util/t3code-openrc
.agents/skills/overlay-tools/bin/qa-ebuild --overlay-path . --all --json
.agents/skills/overlay-tools/bin/qa-ebuild --overlay-path . --changed-since origin/master
```

Choose exactly one selection method: explicit targets, `--all`, or
`--changed-since REF`. With no targets or an empty selection, the result is
INCONCLUSIVE, never PASS. An empty or whitespace-only `--changed-since` reference
is invalid, even when explicit targets are also supplied. The overlay must contain
`profiles/repo_name` and `metadata/layout.conf`.

## Selection and safety

Explicit targets can be unversioned `category/package` atoms, absolute package
paths inside the overlay, package ebuilds, `metadata.xml`, `Manifest`, `files/`
or paths under `files/`, or `metadata/md5-cache/category/package-version`.
All targets must exist. Duplicate targets select the package once. A target
selects all of that package's ebuilds, not just the targeted version.

The tool validates paths before reading their contents. Traversal, outside
paths, symlink loops and symlinks escaping the overlay are input errors. It
validates the selected package trees, related metadata cache paths and repository layout
path before checking package contents. Manifest-provided paths must be relative;
AUX names are relative to `files/`. The tool never opens DIST bytes.
This is filesystem validation, not an OS sandbox. Do not modify the checkout
concurrently with a scan; validation cannot prevent a concurrent symlink swap.

`--all` finds current packages containing ebuilds. `--changed-since REF` requires
the overlay to be the Git worktree root. It resolves an explicit commit base
and unions these paths:

- Worktree changes compared directly with that base, including committed changes.
- Index changes compared with the base.
- Unstaged changes compared with the index.

There is no implicit branch, remote fetch, merge-base or three-dot comparison.
Rename paths include both sides. Deleted files still select a surviving package.
Fully deleted packages and unrelated paths appear in `skipped`; they cannot be
examined. Changes to layout configuration, profiles or eclasses select all
current packages because they can affect shared behavior. Cache changes map to
current package atoms. Untracked files do not select packages through Git diff;
`skipped` lists them. Use explicit targets or `--all` to include a new package.
Untracked files inside an already selected package are still locally checked.
Git commands disable external diff, textconv, hooks, fsmonitor, untracked-cache
writes, automatic diff index refresh and optional index locks. Every command
passes `-c diff.autoRefreshIndex=false`, overriding local, included and worktree
configuration without changing it. `GIT_OPTIONAL_LOCKS=0` alone does not stop
Git diff from writing cached stat information and the index checksum when staged
content differs from same-size worktree bytes restored to the base. Selection
preserves index bytes and mtime, including on repeated invocations. Each comparison
uses `git diff --numstat -z --no-renames`, not the stat-only `--name-only` or
`--raw` shortcuts. With automatic index refresh disabled, those shortcuts can
report identical-byte rewrites as changes. Numstat verifies Git-normalized content
without updating the index. Timestamp-only shared or package rewrites do not
select packages. CRLF and ident attribute normalization still follows Git;
selection does not substitute a raw-byte comparison or execute clean/process
filters. Binary changes, executable-mode changes, symlink target and file-type
changes, empty-file additions/deletions and both sides of renames remain changes.
NUL-delimited records preserve tabs, newlines and non-UTF-8 filenames. All three
comparisons remain necessary: staged content restored to the base in the worktree
still selects its package through the index/base and worktree/index comparisons.
They strip inherited `GIT_*` overrides, ignore
host global/system config and attributes, and disable all transport protocols,
lazy fetching and terminal prompts. Selection always uses the requested checkout;
a missing commit in a partial repository stays inconclusive without fetching.
Before any diff, selection reads effective repository configuration, including
included files and worktree configuration, recursively in every populated
submodule found in the cached index. Cached gitlinks are read without an index
refresh. Each child path must remain inside its parent and the overlay, and
must identify the expected Git worktree root. Malformed or unsafe populated
children make selection INCONCLUSIVE before comparisons. Unpopulated gitlinks
are not initialized. `submodule.recurse=false` alone does not prevent Git diff
from inspecting dirty children. Any configured clean or process filter
makes selection INCONCLUSIVE with `selection` coverage `unchecked`. No diff or
filter runs. This deliberately rejects even an unused or empty filter definition.
Disabling filters could change Git's clean-equivalence and misidentify changed
inputs. Use explicit targets or `--all` instead, neither of which invokes Git.

## Checks and coverage

- Full Manifests validate local EBUILD, AUX and MISC records, byte sizes and every
  listed supported hash. Local files without a required record, listed files
  that do not exist, duplicate records and malformed records are findings.
  This works for packages with an empty or absent SRC_URI as well.
- `thin-manifests = true` in `metadata/layout.conf` does not require absent local
  records. Any local records that are present still get size/hash checks.
  A thin Manifest may be absent, including for packages with no distfiles.
  The tool does not infer that SRC_URI is empty or that DIST records are complete.
  Coverage reports `thin-records-only` for a present Manifest, or `unchecked` when
  any selected thin Manifest is absent, with a skipped local-coverage explanation.
  An absent full Manifest remains a finding.
- `metadata.xml` must exist, parse as XML and have a `pkgmetadata` root. This is
  not DTD validation, and the tool does not fetch the Gentoo DTD. Unknown or
  unsupported XML encoding declarations are metadata findings, not crashes.
- Every ebuild needs its corresponding md5-cache file. Cache fields must use
  key/value syntax without duplicate keys. The `_md5_` field must match the
  ebuild's bytes. Cache files without a corresponding ebuild are findings.
  This is not validation of expanded dependencies or eclass cache freshness.
  This byte fingerprint matches Portage's `eclass_cache.hashed_path.md5` and
  pkgcore's md5-cache validation with Snakeoil `LazilyHashedPath.md5`; it is not
  a checksum of expanded metadata or a normalized ebuild.
- Cheap header and literal EAPI checks are advisory. Bash `--noprofile --norc -n`
  reads the ebuild on stdin, with startup-hook environment variables removed.
  Syntax errors are findings; unavailable Bash makes the scan inconclusive.
  It never sources or executes the ebuild. A literal text check does not expand
  Bash, SRC_URI, inherit statements or conditional metadata.

Patch application is deliberately not inferred from comments, quoted strings,
`PATCHES` text or eclass names. Retention policies are not checked or changed.
The authorized repository policy is two previous versions plus the new version.

Remote DIST bytes are always **unchecked**. A PASS means only the selected local
checks passed. It does not prove fetchability, expanded metadata correctness,
patch application, build success or install/runtime correctness.

## Optional pkgcheck

`--pkgcheck` explicitly opts into an external tool, only after local input
validation. Both CLIs reject abbreviated long options. Only the exact
`--pkgcheck` flag opts into this tool; prefixes such as `--pkg` are input errors
and launch no checks. Nothing installs it automatically. The argv is scoped to the exact
overlay and selected atoms, uses `-f latest`, disables pkgcheck configuration
loading with `--config no` and addon caches with `--cache no`, requests a required
sandbox with `--sandbox yes`, and never enables `--net`. Required sandbox support
is version-dependent; unsupported flags or confinement are not treated as a
successful scan. Latest filtering avoids intentional old-version retention
warnings; local checks still cover every ebuild.

External pkgcheck/pkgcore can generate metadata by sourcing ebuilds and can write
its metadata caches. The optional adapter therefore does not extend the local
no-execution/read-only guarantee to the external program. Use this opt-in only
on trusted ebuilds with a compatible installed pkgcheck; do not use it to scan
untrusted code. The local default never launches pkgcheck. The tests exercise
this adapter using executable fixtures, not by sourcing repository ebuilds.

A missing tool, a timeout or an invocation failure is INCONCLUSIVE. Exit 1 from
pkgcheck is a finding, and other nonzero exits are inconclusive. Captured output
is included in findings; output on a zero exit is advisory. The adapter requests
`--exit error`, so warnings alone do not fail the scan.

## Controller integration

The local `qa-ebuild-info` pre-commit hook accepts exact filenames, deduplicates
package targets and reports all local findings. Its filter covers package
`*.ebuild`, `Manifest`, `metadata.xml`, `files/`, metadata cache entries, layout
configuration, profiles and eclasses. Shared inputs select all current packages.
A deleted filename still maps to its surviving package when passed to the adapter;
pre-commit itself normally omits fully deleted files. CI's Git selection includes
deletions. The hook is serial and verbose so a successful wrapper does not hide
its report. It never calls uv or pkgcheck.

```bash
.agents/skills/overlay-tools/bin/qa-ebuild-check --overlay-path . --informational \
  dev-util/example/metadata.xml
```

Only the exact `--informational` option enables the zero-exit wrapper.
Abbreviations such as `--info` are input errors and do not enable that override.
A token after the `--` option terminator or in the value position for
`--overlay-path` or `--changed-since` never enables it. This applies to parser
errors and the missing-environment guard too. A separate explicit flag before
`--` still enables the informational wrapper when another argument is invalid.
With that flag, this wrapper exits `0` after displaying PASS, FAIL or
INCONCLUSIVE and the actual QA exit. Missing environment and empty selection
are still explicitly inconclusive.
The wrapper's success means only that this informational hook does not block a
commit. Use `qa-ebuild` for the authoritative direct CLI contract.

CI runs `qa-ebuild-check --changed-since REF` without the informational flag.
Selection uses the exact shared QA filename mapping, not every arbitrary file
inside a package. For a successful Git selection with no current targets it prints
`not applicable: no package targets changed` and exits `0`, with no package PASS
claim. Skipped unrelated and fully removed package paths remain visible.
Invalid input, unresolved bases, missing Git, missing environment or unavailable
Bash during a scan remain nonzero. Findings exit `1`; inconclusive scans exit `2`.
An empty `--initial` scan also exits `2`.

The workflow checks out full history and passes GitHub's PR base SHA or push
`before` SHA through environment variables, never shell interpolation. It compares
directly to that base, not a merge base. An all-zero push `before` means the first
push and runs `--initial` to check every current package. It does not create an
empty-tree object or alter Git. Neither integration opts into external pkgcheck.
Existing ruff, formatting, type and pytest CI gates remain in place.

Controller tests execute the public adapter, the exact local hook with the real
pre-commit runner, and the workflow's shell body under PR, push, first-push,
findings, missing-tool, missing-base and hostile-input conditions. The local-hook
replay extracts the same hook configuration without initializing the unrelated
remote ruff hook. YAML parsing and shell replay do not prove a live Actions run;
run actionlint too when available.

## Reports and exits

`--json` emits one object on stdout with `status`, `examined_count`, `packages`,
`selected_count`, `selected_packages`, `package_coverage`, `findings`, `skipped`,
`coverage` and `inconclusive`, including when the provisioned environment is
missing. In that case both counts are zero, both package lists are empty,
`package_coverage` is empty, and no local check claims execution. Each finding identifies its
check, path, message and severity. Coverage names the limited checks actually
performed, and always marks `remote_dist` and `bash_metadata` as unchecked.
Human-readable output also states the unchecked remote and expanded metadata
coverage and displays the selected count and each package's coverage.

`selected_packages` lists every selected atom in scan order. `packages` and
`examined_count` include only packages whose local scan reached its end, even
when that scan found errors. An input read or UTF-8 decoding failure reports
the exact file path and makes the result INCONCLUSIVE with exit 2. During a
package scan, the report also identifies the interrupted package as incomplete.
Earlier findings and completed package coverage remain in the report.
`package_coverage` distinguishes checks completed before the interruption from
`unchecked`, `partial` or `not-run` work. Later packages stay `not-run`.
Aggregate `coverage` uses `partial` where only part of the selection received
a check, rather than claiming that every selected package was checked.
Bash syntax coverage is `unchecked` when no version's syntax check completed,
`partial` when some checks completed but others were unavailable or failed to
run, and `checked` only when every version's check completed. A later successful
check cannot erase earlier missing coverage. Aggregate Bash coverage also stays
`unchecked` when none completed and later packages remain `not-run`.
Bash lookup, launch and timeout failures report the affected ebuild and make
the result INCONCLUSIVE with exit 2.
A completed check that finds invalid syntax still counts as `checked` coverage
and reports a syntax finding. Literal header and EAPI coverage is independent
of Bash availability; it becomes `partial` if a later ebuild cannot be read.
Manifest coverage still records absent thin Manifests as `unchecked` and absent
full Manifests as `missing-manifest`. No interrupted scan becomes a PASS.

- `0`: at least one package examined; selected local checks passed. Advisory
  findings may remain.
- `1`: local findings or a pkgcheck findings exit.
- `2`: invalid input, environment failure, empty selection or inconclusive work.

These checks do not repair files, change version retention, run Portage phases,
modify `/etc`, install packages, or update Git status.
