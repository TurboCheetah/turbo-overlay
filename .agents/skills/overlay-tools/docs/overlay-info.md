# overlay-info

Generate context from the checkout being inspected, rather than from a saved
package list or a previous run. Requires Python 3.11 or newer. Git is optional.

```bash
# Install the tools and development dependencies once.
uv sync --locked --group dev --project .agents/skills/overlay-tools

# Run from the checkout root, or any directory beneath it.
.agents/skills/overlay-tools/bin/overlay-info

# Select a checkout explicitly, regardless of the current directory.
/path/to/tools/bin/overlay-info --overlay-path /path/to/checkout --json

# Complete lists, exact configuration text, and Portage masks.
/path/to/tools/bin/overlay-info --overlay-path /path/to/checkout --json --full
```

The relative examples run from the checkout root. The wrapper uses its own
tools project with `uv run --offline --no-sync`. It never installs
or updates dependencies during inspection. Python starts in isolated mode,
ignoring the caller's import directory, `PYTHONPATH`, and user site packages.
Imports use the source beside this wrapper, not another installed checkout.
The wrapper also passes `-B`, so inspection never creates Python bytecode or
`__pycache__` directories in that source tree. Isolated mode ignores the
`PYTHONDONTWRITEBYTECODE` environment variable, so the explicit flag is required.
You can also run
`python -B -m overlay_tools.cli.overlay_info` in the tools environment.

## Output and bounds

Text is the default. `--json` emits one JSON object with `schema_version: 1`.
Both formats report the same information, sorted independently of directory
iteration order. Each invocation reads the current files. There is no context
cache, generated inventory file, or installed Portage configuration change.

Every checkout-derived list has this shape:

```json
{
  "total": 0,
  "truncated": 0,
  "items": []
}
```

The example shows an empty list. `total` counts the complete list and
`truncated` counts omitted items, not a boolean. Summary mode
shows at most **10 items per list**, including categories, packages, masters,
EAPI settings and counts, Manifest hashes, local eclasses, policy exclusions,
and active exclusions. Every emitted string contains at most **240 Unicode
codepoints**. Values retain their exact prefix, without normalization or an
ellipsis. `string_truncations` lists each shortened value by JSON Pointer,
with `characters` giving its original length and `truncated` its exact omitted
codepoint count. This metadata is complete, not sampled. Policy reasons also use
`reason_characters` and `reason_truncated` reporting the original length and
omitted codepoint count. Each successful summary, text or JSON, is at most
**131,072 UTF-8 bytes**, including escaping, formatting, metadata and the final
newline. If serialization would exceed this budget, the command exits `2`,
emits no stdout and recommends `--full`. `limits` declares all caps in JSON.
These output bounds do not cap input file sizes or processing memory.

`--full` removes all caps, sets the corresponding `limits` values to `null`,
and adds complete ebuild paths and raw UTF-8 configuration text. CRLF, LF,
lone CR and mixed newlines are preserved exactly, including in masks. Both
formats quote and escape these strings; decoding the JSON string recovers the
original text. It is the only mode that reads or emits Portage masks. Full
output can be large.

## JSON fields

| Field | Meaning |
| --- | --- |
| `mode` | `summary` or `full` |
| `checkout.root` | Canonical absolute overlay root, or its explicitly marked prefix in summary mode. Use `--full` for the exact path if `/checkout/root` appears in `string_truncations` |
| `checkout.name` | Name read from `profiles/repo_name` |
| `checkout.git` | Inspection status, exact HEAD revision, dirty boolean, and reason if unchecked |
| `inventory` | Actual category, package and ebuild totals, category counts and package samples |
| `configuration` | Layout path and presence, masters, explicit Manifest settings, and EAPI information |
| `local_eclasses` | Names of local `eclass/*.eclass` files, not inherited master eclasses |
| `update_policy` | Policy path and presence, exclusions with reasons, and exclusions matching current packages |
| `verification` | Existing QA and Python verification commands with their working directories, never executed |
| `string_truncations` | Exact lengths and omitted codepoints for each shortened value. A listed identifier or command is incomplete and must not be used as an exact token |
| `inventory.ebuilds` | Full mode only, checkout-relative ebuild paths |
| `full_configuration` | Full mode only, exact text of `metadata/layout.conf`, `profiles/repo_name`, `profiles/eapi`, and `profiles/package.mask` or its directory files |

Root discovery stops at the nearest `profiles/repo_name` marker, even if it is
broken or unreadable. Inventory uses the existing `find_packages` and
`find_ebuilds` discovery rules. Packages must have discoverable ebuild filenames.
Hidden and reserved directories, including `deprecated`, do not contribute to
inventory. A category counts only if it contains a discoverable package.
Inventory includes masked packages and maintenance-excluded packages. Neither
policy changes what exists on disk.

Manifest fields reflect explicit `thin-manifests`, `sign-manifests`,
`use-manifests`, `manifest-hashes` and `manifest-required-hashes` settings.
Absent scalar settings are `null`; absent lists are empty. These are local
settings, not resolved Portage defaults or master configuration.

EAPI information includes `profiles/eapi`, literal ebuild EAPI counts, and
layout's banned, deprecated and testing EAPI lists plus banned and deprecated
profile EAPI lists. An ebuild EAPI must be a literal number on its first
non-comment, non-blank line. Quoted literals and trailing comments are accepted.
Missing or dynamic declarations increment `unresolved_count`; the tool does not
guess an EAPI or evaluate shell expressions.

The update policy is `metadata/update-exclusions.json`, using the same validator
as the maintenance commands. Missing policy means no exclusions. `active_count`
and `active_exclusions` refer to policy atoms that are currently discoverable
packages. They do not mean Portage installation eligibility. Portage masks are
separate and never inferred as update exclusions.

## Checkout selection and Git

`--overlay-path` takes precedence over the current directory. It must identify
an existing directory; normal overlay discovery then searches its ancestors.
The returned canonical root distinguishes same-name checkouts. A missing path
or a regular file is an error, even if an ancestor is an overlay.
A broken, unreadable or directory marker at a nearer root is an error, never
permission to substitute a parent overlay's identity.

Git inspection is read-only. It checks that Git's worktree root matches the
overlay root, reads the exact HEAD object name, and uses porcelain status to
include staged, unstaged, untracked and submodule changes. Ignored files do not
make the checkout dirty. It does not list changed paths, fetch, checkout, commit,
or refresh the index. It disables optional Git locks, fsmonitor hooks and the
untracked cache, blocks lazy fetches and all transport protocols, and ignores
inherited Git repository selection variables and host global/system
configuration. Before running status, it reads Git configuration with includes
and worktree settings enabled, and checks populated submodules recursively
using cached gitlinks. Any nonempty `filter.*.clean` or `filter.*.process`
command makes Git inspection `unchecked`, with `revision` and `dirty` set to
`null` and an explicit reason. This refusal applies even to unused or
overridden commands. It never runs a filter to decide whether inspection is
safe. Empty filter commands do not prevent inspection. A configuration or
submodule inspection failure also leaves Git unchecked, without falling back
to status. Each Git command has a ten-second timeout.

If Git is absent, the directory is not a Git checkout, HEAD is unavailable, or
inspection fails, `status` is `unchecked`, `revision` and `dirty` are `null`, and
`reason` explains why. An ancestor checkout's revision is not substituted.
Git metadata and file inventory are separate reads, not an atomic snapshot of a
checkout being changed by another process.

## Errors and safety

Success exits `0`. Invalid arguments, roots, configuration or policy exit `2`,
print a diagnostic to stderr, and emit no partial context on stdout. The wrapper
exits `127` if `uv` is missing; missing environment dependencies remain an
explicit `uv` failure. Missing optional layout, profile EAPI or policy files
remain distinguishable from malformed or unreadable files. Duplicate layout
keys and duplicate policy atoms fail rather than silently choosing a value.
Malformed masks fail when requested with `--full`. Full mode scans every real
subdirectory of `profiles/package.mask` and reports its files in sorted order.
Unreadable directories, including nested directories, and scan or file-stat
errors exit `2` with the failing path on stderr and no stdout. Directory symlinks
are errors, not omitted subtrees or permission to traverse outside the mask tree.
File symlinks retain ordinary file reads; broken links and symlink loops fail.
Summary mode does not inspect masks, even when their directories are unreadable.

The command never sources ebuilds or eclasses, imports checkout scripts, runs
verification commands, makes upstream API requests, or edits installed
configuration. The verification commands are suggestions, not checks performed
by this report. Package and ebuild paths in them are placeholders. The phase-test
command requires Docker and a trusted ebuild; do not run it on unreviewed code.
This is checkout context, not a build, dependency, or QA verdict.
