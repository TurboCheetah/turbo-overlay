# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit readme.gentoo-r1

DESCRIPTION="OpenRC adapter for user-owned T3 Code server runtimes"
HOMEPAGE="https://t3.codes/"
S="${WORKDIR}"

LICENSE="GPL-2"
SLOT="0"
KEYWORDS="~amd64"
IUSE="tailscale"

RDEPEND="
	app-misc/jq
	sys-apps/coreutils
	sys-apps/openrc
	sys-apps/util-linux[su]
	|| ( sys-libs/glibc sys-libs/musl[-headers-only] )
	tailscale? (
		net-misc/curl
		net-vpn/tailscale
	)
"

src_install() {
	newinitd "${FILESDIR}/t3code.initd" t3code
	newconfd "${FILESDIR}/t3code.confd" t3code
	exeinto /usr/libexec
	doexe "${FILESDIR}/t3code-openrc"

	readme.gentoo_create_doc
}

pkg_postinst() {
	elog "This package installs an adapter only, not T3 or its per-user runtime."
	elog "As the service user, install upstream T3 and explicitly initialize its state."
	elog "Read /usr/share/doc/${PF}/README.gentoo* before configuring the service."
	ewarn "Review config-protect updates and any custom /usr/local/libexec wrapper."
	ewarn "No existing service is automatically replaced, enabled or restarted."
}
