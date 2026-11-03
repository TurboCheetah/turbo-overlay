# Manual Update Checks

Package-specific procedures for packages that `check-updates` reports as
`manual-check`.

## media-video/hayase-bin

- Why manual: GitHub releases and the site changelog are not authoritative for
  the latest Linux package.
- Sources of truth:
  - `https://api.hayase.watch/latest`
  - `https://api.hayase.watch/files/latest-linux.yml`
- Check procedure:
  1. Fetch `https://api.hayase.watch/latest`.
  2. Verify that the response contains the `latest-linux.yml` key.
  3. Fetch the advertised `latest-linux.yml` URL.
  4. Read the YAML `version` field and use it as the next Gentoo `PV`.
  5. Confirm the YAML `files` list contains `linux-hayase-<version>-linux.deb`.
  6. Use the `.deb` entry metadata for verification, including `sha512` and
     `size`.
- Version mapping: upstream version maps directly to Gentoo `PV`.
- Validation rule: only bump when `/latest` advertises `latest-linux.yml` and
  the YAML contains the matching Linux `.deb` entry.
- Pitfall: do not trust GitHub latest-release data for current Linux builds.

