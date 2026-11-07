# t3code-openrc design notes

Maintainer notes, not installed. User-facing steps are in `files/README.gentoo`.
Carried over from the guide this package used to install; not re-audited against the scripts.

- Adapter version `1` is independent of upstream T3 versions. No runtime files
  are part of the package image.
- Root never executes a user-owned T3 executable. The root preflight uses `su`
  to run only the fixed packaged helper as the service user.
- The wrapper resolves `activeVersion` on every invocation and execs that
  version's `t3 __service-launcher`. OpenRC supervises the launcher with
  bounded respawn; upstream handles self-update and rollback.
- Logs: only after privilege drop does `run` create `userdata/logs` and append
  to `userdata/logs/openrc.log`. Root hooks never create logs, touch files or
  chown user-controlled paths.
- `initialize` requires a non-root uid, validates the executable and matching
  sentinel, and atomically publishes a private protocol-3 state file only if
  none exists (any existing state, including malformed JSON, directories and
  dangling symlinks, is refused). `check` reads the JSON, exact active version,
  executable and sentinel; it makes no files and never executes T3.
- Fixed settings: bind `127.0.0.1`, mode `web`, browser opening off,
  working-directory project bootstrap off, T3's internal Tailscale Serve off.
  The same options are exported to preflight and launch.
- Tailscale: OpenRC is the sole owner of the Serve mapping. `start_post` waits
  for the loopback environment descriptor before creating the HTTPS mapping and
  reading it back; `stop_post` removes only the configured port. Readiness
  retries (`t3code_proxy_retries`, 1 to 60) use one-second gaps and bounded
  command timeouts, and failures return a nonzero hook result.
- Package tests do not start a live service or exercise an upstream update.
