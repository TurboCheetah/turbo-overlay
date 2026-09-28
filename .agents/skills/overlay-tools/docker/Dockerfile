# Disposable Gentoo stage3 environment for overlay ebuild phase tests.
# Intentionally tracks the latest stage3 and Portage snapshot; `test-ebuild
# --build` rebuilds with --pull --no-cache to refresh both.
FROM --platform=linux/amd64 gentoo/stage3:latest

RUN emerge-webrsync

COPY run-ebuild /usr/local/bin/run-ebuild
RUN chmod 0755 /usr/local/bin/run-ebuild

ENTRYPOINT ["/usr/local/bin/run-ebuild"]
