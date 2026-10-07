# turbo-overlay
Turbo's Gentoo Overlay

```sh
$ emerge app-eselect/eselect-repository
$ eselect repository add turbo-overlay git https://github.com/TurboCheetah/turbo-overlay
$ emerge --sync turbo-overlay
```

## Migrations

### T3 Code desktop and OpenRC

`dev-util/t3code-bin` and `dev-util/t3code-nightly-bin` are deprecated and
masked as of 2026-10-07. Their six existing ebuilds and Manifests remain for
rollback, but this overlay no longer bumps their versions. A mask does not
uninstall an existing package. There is no atom move or desktop replacement
package.

Portage owns only the separate `dev-util/t3code-openrc` service adapter for
new T3 Code setups. It does not install the desktop application or CLI runtime.
The upstream installer and updater own the per-user runtime. The adapter starts
at local version `1`; upstream `pingdotgg/t3code` releases and nightly tags do
not change that version.

For an existing desktop installation:

1. Keep the installed Portage desktop package until an upstream desktop build
   works for you. Back up your T3 Code user data and settings before testing.
2. Choose the appropriate Linux desktop release or AppImage from
   [upstream releases](https://github.com/pingdotgg/t3code/releases). Download
   it into a user-owned directory such as `~/Applications/`, verify the
   downloaded artifact using upstream's published information, and launch
   that copy directly. Check your projects, provider access, and desktop
   integration. Do not let an installer overwrite Portage-owned files under
   `/opt` or `/usr`.
3. Review any old local `t3code` wrapper and launcher entries so they do not
   keep invoking the Portage desktop copy. Do not delete a wrapper until you
   know whether it is local or package-owned.
4. Follow [upstream's update guidance](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md)
   for that installation. This overlay does not guarantee that a Linux desktop
   artifact updates itself.
5. Only after the upstream desktop is verified, and with separate approval,
   unmerge the old desktop package if you no longer need it. No migration,
   adapter install, or repository update automatically uninstalls it.

For an OpenRC-hosted CLI runtime, read the
[adapter package guide](dev-util/t3code-openrc/files/GUIDE.md) first:

1. Review the [upstream CLI installation instructions](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md#command-line)
   and [installer source](https://t3.codes/install.sh). Run a reviewed installer
   only as the intended non-root service user, with the intended user home and
   `T3CODE_HOME`. Do not run it through `sudo`, pipe it directly into a shell,
   or point it at Portage-owned `/opt` or `/usr` paths. Download the runtime
   before initializing the adapter. Do not use upstream's systemd service
   installer for this OpenRC setup.
2. Configure that user's identity, home, and runtime home in
   `/etc/conf.d/t3code`, following the package guide. Review Portage
   config-protected changes with your usual configuration-update tool rather
   than overwriting local settings. Review any old local OpenRC wrapper before
   switching to the packaged adapter.
3. As that user, initialize the downloaded exact version. The following is a
   template, not a command to paste with the placeholder still present:

   ```sh
   T3CODE_HOME="$HOME/.t3" /usr/libexec/t3code-openrc initialize '<exact version>'
   ```

   Use the exact installed SemVer, not `latest`, a release tag, or a Gentoo
   `_pre` version. Initialization does not download or replace the runtime.
4. Start or restart the service only as a separate, approved administration
   action. An `emerge` of the adapter does not install upstream source in
   `pkg_postinst`, download a runtime, start or restart a service, or uninstall
   an old desktop package.

Update automation uses [`metadata/update-exclusions.json`](metadata/update-exclusions.json),
separately from Portage masks. Both desktop atoms and the locally versioned
adapter are excluded before upstream lookups. Daily and weekly workflow
schedules stay unchanged; other packages continue to be checked.

### Vesktop

- **deprecated/vesktop-bin** was removed from this overlay. Install
  `net-im/vesktop` from the [GURU overlay](https://wiki.gentoo.org/wiki/Project:GURU)
  instead (`eselect repository enable guru && emerge --sync guru`).
