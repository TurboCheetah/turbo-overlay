# Disposable Gentoo stage3 environment for overlay ebuild phase tests.
# Intentionally tracks the latest stage3 and Portage snapshot; `test-ebuild
# --build` rebuilds with --pull --no-cache to refresh both.
FROM --platform=linux/amd64 gentoo/stage3:latest

RUN emerge-webrsync

# test-ebuild bind-mounts the checkout's run-ebuild here on every run, so the
# image never carries a stale copy of the script.
ENTRYPOINT ["bash", "/usr/local/bin/run-ebuild"]
