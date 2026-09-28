# Disposable Gentoo stage3 environment for overlay ebuild phase tests.
FROM gentoo/stage3:latest

RUN emerge-webrsync

COPY run-ebuild /usr/local/bin/run-ebuild
RUN chmod 0755 /usr/local/bin/run-ebuild

ENTRYPOINT ["/usr/local/bin/run-ebuild"]
