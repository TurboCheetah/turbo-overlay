# sys-apps/rex-bin

Rex is Superlogical's terminal multiplexer. Upstream has no Gentoo support and
ships a closed-source prebuilt binary, so `sys-apps/rex-bin` repackages it the
way upstream's own `.deb`/`.rpm`/`.apk` packages lay it out, with OpenRC
services instead of systemd units. `acct-group/rex` and `acct-user/rex` create
the `rex` group and the locked, `nologin` system user (home `/var/lib/rex`).

| Path | What |
| --- | --- |
| `/usr/bin/rex` | server and CLI |
| `/etc/init.d/rex`, `/etc/conf.d/rex` | shared server (one server, runs as `rex`) |
| `/etc/init.d/rex-login-server`, `/etc/conf.d/rex-login-server` | login server (one server per account, PAM login) |
| `/etc/rex/accounts.jsonc` | login server accounts, empty (admits nobody) |
| `/etc/pam.d/rex-login-server` | PAM policy, only with `USE=pam` (default) |
| `/usr/share/terminfo/x/xterm-rex` (+ `r/rex` alias) | compiled from `rex terminfo show` at build time |
| `/var/lib/rex` (0700), `/var/log/rex` (0750) | state (also the `rex` home) and logs, owned by `rex:rex` |
| `/etc/logrotate.d/rex` | `copytruncate` rotation of `/var/log/rex/*.log` and `/var/log/rex-login-server.log` |

Deliberately **not** installed: `/etc/ssh/ssh_config.d/50-rex.conf`. That ssh
fallback breaks *every* `ssh` command on OpenSSH older than 7.8, and an ebuild
cannot know what that is on the machines you ssh from. `rex terminfo setup`
checks the ssh version before writing it; see [Terminfo](#terminfo).

## Install

### Path A: binary only, no Portage

Upstream's script supports Gentoo only through `--tarball`:

```sh
curl -fsSL https://pkgs.superlogical.com/rex/install.sh | doas sh -s -- --tarball
```

It fetches `https://blob.superlogical.com/api/releases/rex/<channel>`, picks
`rex-linux-<amd64|arm64>[-musl].tar.gz`, verifies the sha256 from that JSON and
installs **only** `/usr/local/bin/rex`: no services, no `rex` account, no
terminfo, no PAM policy. Run it again to update. The default channel is
`stable`, which does not exist (404); per upstream's docs it falls back to
`preview` (pass `--channel preview` to be explicit). `--dry-run` shows what it
would do (it still wants root).

Do not combine this with Path B: `/usr/local/bin` normally precedes `/usr/bin`
in `PATH`, so the tarball copy would shadow the packaged one. Without a
service, use a personal server: `rex server start` (socket in
`$XDG_RUNTIME_DIR/rex`, state in `~/.local/state/rex`, no root, no account).

### Path B: `emerge`

```sh
eselect repository add turbo-overlay git https://github.com/TurboCheetah/turbo-overlay
emerge --sync turbo-overlay
emerge --ask sys-apps/rex-bin
```

You need `~amd64`/`~arm64` in `ACCEPT_KEYWORDS` (or `sys-apps/rex-bin ~amd64` in
`package.accept_keywords`) and `ACCEPT_LICENSE` accepting `all-rights-reserved`.
`USE="-pam"` drops `sys-libs/pam` and the PAM policy and starts the login server
with `--pam=false` (shadow-based checks, no login session). The binary only
`dlopen()`s libpam, which is why the ebuild lists it explicitly.

## Running it under OpenRC

```sh
rc-update add rex default            # start at boot
rc-service rex start                # or: stop, restart, reload, status
rc-update del rex default            # stop starting at boot
```

* `/etc/conf.d/rex` holds `command_args`, the full command line after
  `/usr/bin/rex`. The default is upstream's exact one:
  `server run --listen /run/rex/server.sock --state-dir /var/lib/rex --socket-mode 0660 --log-format text`.
  Keep those four options and add to them. `rc-service rex reload` runs
  `rex config reload` (re-reads `/var/lib/rex/.config/rex/init.lua`).
* The init script runs the server as `rex:rex`, creates `/run/rex` (`rex:rex`,
  0750: only the group can enter, which protects the 0660 socket),
  `/var/lib/rex` and `/var/log/rex` with `checkpath`, and keeps a pidfile at
  `/run/rex.pid`. Logs go to `/var/log/rex/server.log`.
* **Using it**: put users in the `rex` group (`gpasswd -a alice rex`, log in
  again) and point their client at the socket:
  `rex -S /run/rex/server.sock ls`, or `export REX_SERVER=/run/rex/server.sock`.
  Access to the shared server is access to the `rex` account: anyone in the
  group can see and type into every session.
* **Give `rex` a real shell**, otherwise every terminal prints "This account is
  currently not available." and exits. The account stays locked (no password).
  Set `ACCT_USER_REX_SHELL="/bin/bash"` in `make.conf` and re-emerge
  `acct-user/rex`: `acct-user.eclass` reapplies the configured shell on every
  reinstall, so a bare `usermod --shell /bin/bash rex` only lasts until then.
  Use `usermod` as well if you do not want to wait for the re-emerge.
* OpenRC has no socket activation; the service starts at boot instead.
* `rex` and `rex-login-server` use `/run/rex` differently and refuse to run
  together; each init script checks the other (`start_pre`). Stopping the login
  server leaves the per-account servers it started running, and `rex` refuses
  to start while `/run/rex` still holds their directories: stop those servers
  or reboot first.
* Restarting, stopping or upgrading ends every program in every session. The
  layout of sessions is restored from `/var/lib/rex`; running programs are not
  (they get a fresh shell). A running server keeps the old binary after an
  upgrade, so restart it when nobody is working.
* A crashed daemon shows as `crashed` to root and OpenRC needs `restart` (not
  `start`) to recover; stale sockets are handled by Rex itself. Only root sees
  `crashed`: non-root `rc-service rex status` keeps reporting `started` after
  a crash. Use root for status.

### Login server (alternative to the shared server)

```sh
$EDITOR /etc/rex/accounts.jsonc            # ships empty: admits nobody
rc-service rex stop && rc-update del rex default
rc-update add rex-login-server default && rc-service rex-login-server start
rc-service rex-login-server reload         # after editing accounts (SIGHUP, keeps sessions)
```

It runs as root, opens a PAM session (`/etc/pam.d/rex-login-server` includes
`system-login` like sshd does, so elogind sessions work) and runs a Rex server
per account. It only listens on the root-only `/run/rex/login-server.sock`, so
it is only useful with a network listener (see below). Accounts with a
`nologin` shell are always refused. Logs: `/var/log/rex-login-server.log`, outside
`/var/log/rex` on purpose: the login server writes it as root, and the `rex`
account owns `/var/log/rex`.

### Terminfo

On native builds `xterm-rex` is installed system-wide, so `infocmp xterm-rex`
works (a cross-compile cannot run the binary and skips it). A new
terminal still only claims `xterm-rex` once the ssh fallback exists, otherwise
it falls back to `xterm-256color`. Check and fix per account:

```sh
rex terminfo status          # reports what a new terminal would get, and why
rex terminfo setup           # ~/.terminfo + ssh guard + one Include line in ~/.ssh/config
rex terminfo setup --system  # (root) /etc/ssh/ssh_config.d/50-rex.conf for everyone
ssh -G example.com >/dev/null && echo ok     # ssh must still parse its config
```

`setup` refuses to install the guard on OpenSSH < 7.8 and says so. `--dry-run`
shows what would change.

## Upgrading: re-pin to a new preview/snapshot

The pin lives in the ebuild (`MY_COMMIT`, version). Upstream publishes only
`preview` and `snapshot`; `stable` returns 404. This package is not covered by
the overlay's update automation (see `metadata/update-exclusions.json`).

```sh
curl -fsS https://blob.superlogical.com/api/releases/rex/preview | python3 -m json.tool | head -8
#   "version": "0.1.0-preview.14",  "commit": "<40 hex>"

cd sys-apps/rex-bin
cp rex-bin-0.1.0_pre13.ebuild rex-bin-0.1.0_pre14.ebuild   # 0.1.0-preview.14 -> 0.1.0_pre14
$EDITOR rex-bin-0.1.0_pre14.ebuild                         # set MY_COMMIT
ebuild rex-bin-0.1.0_pre14.ebuild manifest                 # fetches all four distfiles
egencache --repo turbo-overlay --update sys-apps/rex-bin
pkgcheck scan -f latest sys-apps/rex-bin
```

Cross-check the Manifest against upstream's own checksums (the
`artifacts[].sha256` in the release JSON; fields `filename`, `arch`, `libc`)
before trusting it. On native builds the ebuild also refuses to build if the
binary's `rex version` does not report exactly the version and commit it pins;
cross-compiles cannot run the binary, so they skip that check (and the
terminfo entry). Then
`emerge --ask sys-apps/rex-bin` and `rc-service rex restart` (ends all
sessions, see above).

The **snapshot** channel's version string (`0.0.1794006277-g2a79a1`) does not
follow the preview scheme and sorts *below* any `0.1.0_pre*`: use a separate
ebuild with the channel in `MY_URI`, `MY_PV` set to what `rex version` prints,
and `MY_COMMIT` set, and mask or drop the preview ebuild.

## Remote access (Mac client to this machine)

Rex's server listens on Unix sockets only. For the Mac client you either forward
the socket over SSH or add a network listener. **`rex server start --extra-listen …`
does not persist its options, so a listener for the service must be on its
`command_args` in `/etc/conf.d/rex`**; restart after editing. A listener that
cannot be bound (VPN down, tailscaled absent, tagged host without
`--tailnet-allow-identified`) stops the whole server from starting.

### 1. SSH socket forwarding (no listener, no open port)

On the Mac:

```sh
ssh -N host -L ~/.ssh/rex-host.sock:/run/rex/server.sock
```

The SSH user on this machine must be in the `rex` group (that decides who may
open the socket), the server must already be running, and the forward lasts as
long as the ssh process. In Rex for Mac use **Add Remote Host…** with the full
path of the local socket, or on the command line:

```sh
rex hosts add host /Users/you/.ssh/rex-host.sock
rex -S host ls
```

### 2. Tailscale Serve (shared or login server)

For the shared server, in `/etc/conf.d/rex` (the login server reads
`/etc/conf.d/rex-login-server` instead: append the same `--extra-listen`
option to its `command_args` and set `rc_need="tailscale"` there too, as the
commented example shows, and restart `rex-login-server`):

```sh
command_args="server run --listen /run/rex/server.sock --state-dir /var/lib/rex --socket-mode 0660 --log-format text --extra-listen tailnet-serve:///run/rex/serve.sock"
rc_need="tailscale"
```

then `rc-service rex restart` and, once:

```sh
tailscale serve --bg --https=443 unix:/run/rex/serve.sock
```

Use `tailnet-serve://` for the Serve socket and **never `unix://`**: with
`unix://` the server starts without error but treats every tailnet caller as the
account running tailscaled (root). The `tailscale serve` target keeps plain
`unix:` (that is Tailscale's syntax). The address is
`https://<machine>.<tailnet>.ts.net`; add it with **Add Remote Host…** or
`rex hosts add build https://<machine>.<tailnet>.ts.net`.

By default only the Tailscale *user that owns this machine* is admitted. A
**tagged machine has no owner**, and then the server refuses to start at all
("owner-only admission admits nobody") until you also pass
`--tailnet-allow-identified`, which admits everyone your tailnet ACLs let reach
it, with full access to the `rex` account. Check `tailscale serve status` first:
Serve config is per node, not per service.

### 3. Built-in Tailscale (Rex joins the tailnet as its own device)

`rex server start --tailscale` works for a personal server. For the service,
append `--tailscale` (optionally `--tailscale-hostname NAME`,
`--tailscale-auth-key-file FILE`, `--tailscale-advertise-tag tag:NAME`) to
`command_args`. The tailnet needs MagicDNS and HTTPS certificates; the device is
`https://rex.<tailnet>.ts.net`; the first sign-in link appears in
`rex server status` and `/var/log/rex/server.log`. Tagged devices also need
`--tailscale-allow-tailnet-members`.

### Other

`--extra-listen http://VPN-ADDR:7297 --allow-anonymous` is for networks you fully
control (anyone who can reach the port has full access).
`--extra-listen tailnet://:7297` listens on the machine's tailnet address.

## Removal

```sh
rc-service rex stop; rc-update del rex default
emerge --ask --unmerge sys-apps/rex-bin    # add acct-user/rex acct-group/rex to drop those too
```

Removal keeps `/var/lib/rex` (sessions, host id), `/var/log/rex`, the `rex`
account/group and any edited config. Unmerging while a server is running warns
and leaves the daemon running on the deleted binary; stop it first, or
reinstall and `rc-service rex restart`.

## Verified when this package was written

Gentoo amd64, glibc 2.44, OpenRC 0.64.1, OpenSSH 10.6.

* Tested end to end: install/remove/reinstall, `USE=pam` and `USE=-pam` images,
  shared server start/stop/restart/reload/crash recovery, socket
  `srw-rw---- rex:rex` in `750 rex:rex` `/run/rex`, access as a `rex`-group
  member and refusal for others, session restore across restart, login server
  with a real PAM session and `nologin` refusal, mutual exclusion, runlevel
  persistence, `xterm-rex` resolution, `--extra-listen tailnet-serve://`
  startup, SSH socket forward, `rex hosts add`.
* **Not** tested: arm64 and musl installs (the distfile checksums are verified
  against upstream's release JSON, the install path is not exercised), built-in
  Tailscale, a real `tailscale serve`, the Mac client and **Add Remote Host…**.
