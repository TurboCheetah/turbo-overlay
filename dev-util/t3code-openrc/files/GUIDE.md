# T3 Code on OpenRC

This package installs an OpenRC adapter, not T3 Code. Adapter version 1 is
independent of upstream T3 versions. Portage owns `/etc/init.d/t3code`,
`/etc/conf.d/t3code` and `/usr/libexec/t3code-openrc`. Upstream owns the
standalone executable, its CLI symlink, runtime versions and updates. The
service account owns its state, database and logs. No runtime files are part
of the package image, and package installation does not download, initialize,
enable, start or restart T3.

## Before replacing an existing service

A custom `/usr/local/libexec/t3code-service` or other local wrapper remains a
separate file. This package neither deletes nor replaces it. Review the old
init script and configuration, back up service state and the database, and
plan an explicit service stop before changing a live deployment. Do not run
two supervisors against the same T3 home.

Portage config-protect may preserve an existing `/etc/init.d/t3code` or
`/etc/conf.d/t3code` and install a protected update instead. Review and merge
those changes deliberately. Installing this package alone does not switch a
custom service to this adapter. Nothing restarts automatically.

Protocol-2 Node runtimes and copied launchers are incompatible. Do not change
their protocol number and pretend that migrates them. `initialize` refuses
any existing state, even malformed JSON, directories and dangling symlinks.
Existing deployments need a separate, backed-up migration of runtime and
state; this package does not perform it.

## Install the nightly runtime as the service user

Choose an existing non-root account with an absolute home directory. Log in
as that account. Install the adapter and its dependencies through your normal
Portage workflow first, but do not enable it yet. The upstream installer below
is a manual user action, never a package hook:

```sh
curl --fail --location --output ./t3-install.sh https://t3.codes/install.sh
# Read the downloaded script before executing it.
less ./t3-install.sh
t3_home="$HOME/.t3" # Choose the runtime home once; use this same path below.
T3CODE_CHANNEL=nightly T3CODE_HOME="$t3_home" sh ./t3-install.sh
"$HOME/.local/bin/t3" --version
```

The official installer downloads a standalone archive into
`$T3CODE_HOME/runtime/versions/<exact-version>/` and writes `.install-complete`
with that exact version. It does not create OpenRC service state. Do not run
`t3 service install` on OpenRC: upstream's Linux service-manager integration
expects systemd. The CLI installation and this adapter are separate steps.

Copy the exact installed SemVer from the version output or upstream release.
Use the upstream spelling, including any `-nightly` and build suffix, without
a leading `v`. Do not use the Gentoo `_pre` spelling or the dist-tag `nightly`.
Then explicitly initialize the already downloaded, complete runtime. If using
a new shell, first set `t3_home` to the same absolute path selected for the
installer and configured as `t3code_home`; do not substitute the default path
when the runtime is elsewhere:

```sh
version='REPLACE_WITH_EXACT_INSTALLED_SEMVER'
T3CODE_HOME="$t3_home" /usr/libexec/t3code-openrc initialize "$version"
T3CODE_HOME="$t3_home" /usr/libexec/t3code-openrc check
```

Initialize and check with the same runtime home you configure as
`t3code_home`; the installer default is `$HOME/.t3`.

`initialize` requires a non-root uid, validates the executable and matching
sentinel, and atomically publishes a private protocol-3 state file only if
none exists. It never downloads, runs the app or overwrites state. `check`
reads the JSON, exact active version, executable and sentinel. It makes no
files and never executes T3.

## Configure and enable explicitly

As administrator, edit `/etc/conf.d/t3code`:

```sh
t3code_user="YOUR_EXISTING_ACCOUNT"
t3code_home="/ABSOLUTE/PATH/TO/THAT/ACCOUNTS/.t3"
```

Quote paths containing spaces. Set `t3code_home` to the exact absolute path
selected as `t3_home` for installation and initialization above.
The adapter obtains `HOME` from the configured
account's passwd entry, sets its working directory to that home, and gives
the unprivileged runtime a PATH containing `$HOME/.local/bin` and
`$HOME/.opencode/bin` followed by `t3code_helper_path`. This includes the
official standalone OpenCode installation without requiring a symlink.
Provider helpers such as Codex, Claude or OpenCode must be
installed for that account. The adapter does not install them.

The default local port is upstream's 3773, configurable with `t3code_port`.
The bind address is always `127.0.0.1`, mode is `web`, browser opening is off,
working-directory project bootstrap is off, and T3's internal Tailscale Serve
integration is off. The same options are exported to preflight and launch.
The root preflight uses `su` to run only the fixed packaged helper as the
service user; it never executes a user-owned T3 executable as root.

After reviewing any config-protect updates, enable and start manually with
your administrator privilege tool:

```sh
rc-update add t3code default
rc-service t3code start
rc-service t3code status
```

The wrapper resolves `activeVersion` again on every invocation and execs
that version's `t3 __service-launcher`. Upstream handles service-managed
updates and rollback; OpenRC supervises the launcher with bounded respawn.
A desktop Portage package update does not update this user runtime.

Only after privilege drop does `run` create `userdata/logs` and append to
`userdata/logs/openrc.log`. Root hooks do not create logs, touch files or
chown user-controlled paths. A missing or incomplete runtime is a startup
failure, not a request to bootstrap it.

## Optional Tailscale Serve

Tailscale is off by default. The disabled path requires neither the Tailscale
package nor a running daemon and never changes Serve state. To enable it,
build this adapter with USE `tailscale`, configure and authenticate tailscaled
separately, and inspect `tailscale serve status` first.

Reserve a dedicated, otherwise unused HTTPS port for this adapter. Do not
share it with another service or configure T3 to manage Serve itself. Set all
three options explicitly, with the target port matching `t3code_port`:

```sh
t3code_tailscale="true"
t3code_tailscale_https_port="9443"
t3code_tailscale_target="http://127.0.0.1:3773"
```

OpenRC is the sole mapping owner. Preflight checks tailscaled readiness;
`start_post` waits for the loopback environment descriptor before creating
the HTTPS mapping and reading it back. `stop_post` removes only the configured
port, never the whole Serve configuration. Readiness uses 10 attempts by
default, configurable with `t3code_proxy_retries` from 1 to 60, with one-second
gaps and bounded command timeouts. Failures report a nonzero hook result.
If a post-start check fails, inspect service status and stop the service
explicitly before retrying. Review any mapping left by an interrupted start.
Changing the proxy port while running requires stopping with the old
configuration first. Never let another service claim this configured port.

## Verify the deployment

Check the actual environment descriptor, not a guessed `/health` path. The
SPA can return HTTP 200 for an unknown path. Require the installed version and
`capabilities.serverSelfUpdate == "boot-service"`:

```sh
curl --fail --silent http://127.0.0.1:3773/.well-known/t3/environment | jq .
```

Inspect process ownership and the user-side log. For Tailscale, read back
`tailscale serve status` and probe the advertised HTTPS hostname and port.
Verify local and HTTPS versions agree. A mapping alone does not prove remote
reachability. Perform one approved OpenRC restart and repeat those checks to
verify persistence. These are deployment checks for the administrator;
package tests do not start a live service or exercise a future upstream update.
