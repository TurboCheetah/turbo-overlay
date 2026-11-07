# Re-pinning sys-apps/rex-bin

Maintainer notes. This file is not installed.

`rex-bin` is a closed-source prebuilt pinned to one upstream release commit, so
it is excluded from update automation (`metadata/update-exclusions.json`) and
re-pinned by hand. Upstream publishes the `preview` and `snapshot` channels
only; `stable` returns 404.

## Bump

1. Read the latest preview release:

   ```sh
   curl -fsS https://blob.superlogical.com/api/releases/rex/preview | python3 -m json.tool | head -8
   ```

   `version` is e.g. `0.1.0-preview.14` and `commit` is a 40-hex SHA. The
   ebuild maps `PV` `0.1.0_pre14` to that version (`MY_PV="${PV/_pre/-preview.}"`).

2. Copy the ebuild to the new `PV` and set `MY_COMMIT`. `MY_PV` and the
   `SRC_URI` follow from `PV` and `MY_COMMIT`.

3. The four distfiles are the `artifacts[]` entries named
   `rex-linux-{amd64,arm64}{,-musl}.tar.gz`, fetched from
   `https://blob.superlogical.com/download/rex/preview/<commit>/<filename>`.
   Ignore the `.zip`, darwin, windows and freebsd entries.

4. Generate the Manifest, which fetches all four:

   ```sh
   ebuild rex-bin-<new>.ebuild manifest
   ```

   Then compare each distfile with upstream's own checksum before trusting it:
   `sha256sum "$(portageq envvar DISTDIR)/rex-bin-<new>-<arch>[-musl].tar.gz"`
   against `artifacts[].sha256` for the matching `filename`.

## Order matters

Manifest first, then `md5-cache`, then `pkgcheck`:

```sh
egencache --repo turbo-overlay --update sys-apps/rex-bin
pkgcheck scan -f latest sys-apps/rex-bin
```

`egencache` needs the checkout to be the configured `turbo-overlay` repository;
for a worktree, pass `--repositories-configuration` pointing that name at it.
Regenerating the cache before the Manifest fails on the stale ebuild hash.

## Safety net and retention

A native build runs `rex version` and dies unless it reports the pinned version
and commit. A cross-compile cannot run the binary, so it skips that check and
the terminfo entry.

Keep the previous versions per the retention policy in `AGENTS.md`. Commit as
`sys-apps/rex-bin: add 0.1.0_pre14`.

## Snapshot channel

The snapshot version string (`0.0.<n>-g<hash>`) does not follow the preview
scheme and sorts below any `0.1.0_pre*`. It needs its own ebuild with the
channel in `MY_URI`, `MY_PV` set to the version `rex version` prints (the
preview-derived `MY_PV` makes a native build reject the snapshot binary) and
`MY_COMMIT` set.
