# Manual Update Checks

Package-specific procedures for packages that `check-updates` reports as
`manual-check`.

## media-video/hayase-bin

- Why manual: GitHub releases and the site changelog are not authoritative for
  the latest Linux package.
- Source of truth: `https://api.hayase.watch/latest`
- Check procedure:
  1. Fetch `https://api.hayase.watch/latest`.
  2. Find the `linux-hayase-<version>-linux.deb` entry.
  3. Extract `<version>` and use it as the next Gentoo `PV`.
  4. Confirm the linked `.deb` URL exists before bumping.
- Version mapping: upstream version maps directly to Gentoo `PV`.
- Validation rule: only bump when the `.deb` entry is present in `/latest`.
- Pitfall: do not trust GitHub latest-release data for current Linux builds.

## x11-terms/warp-bin

- Why manual: the changelog gives the latest base version, but the downloadable
  archive also needs the correct `stable_0N` suffix.
- Sources of truth:
  - `https://docs.warp.dev/changelog`
  - `https://releases.warp.dev/stable/v<MY_PV>/warp-terminal-v<MY_PV>-1-x86_64.pkg.tar.zst`
- Check procedure:
  1. Read the newest version from the Warp changelog, such as
     `0.2026.03.04.08.20`.
  2. Probe archive URLs with `MY_PV` values in order:
     `<version>.stable_01`, then `_02`, then `_03`, and so on.
  3. Stop at the first archive URL that returns `200`.
  4. Set Gentoo `PV` to `<version>_pNN`, where `NN` matches the stable suffix.
- Version mapping:
  - changelog version: `0.YYYY.MM.DD.HH.MM`
  - archive version: `0.YYYY.MM.DD.HH.MM.stable_NN`
  - Gentoo version: `0.YYYY.MM.DD.HH.MM_pNN`
- Validation rule: do not bump until the exact archive URL exists.
- Pitfall: the changelog version alone is not enough; the suffix must be
  confirmed against the release archive.
