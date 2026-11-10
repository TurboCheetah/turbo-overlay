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
	net-misc/curl
	sys-apps/coreutils
	sys-apps/openrc
	sys-apps/util-linux[su]
	|| ( sys-libs/glibc sys-libs/musl[-headers-only] )
	tailscale? ( net-vpn/tailscale )
"

src_install() {
	newinitd "${FILESDIR}/t3code.initd" t3code
	newconfd "${FILESDIR}/t3code.confd" t3code

	exeinto /usr/libexec
	doexe "${FILESDIR}/t3code-openrc"

	readme.gentoo_create_doc
	# Keep it readable with plain less, as the elog footer invites.
	docompress -x "/usr/share/doc/${PF}/README.gentoo"
}

pkg_postinst() {
	# -1 and -1-r1 installed the documentation without ever printing it, and
	# readme.gentoo_print_elog stays silent on upgrades. Print it once when
	# upgrading from either, so those users see it. -1-r2 and later printed it
	# on their first install, so upgrading from them stays quiet.
	local v
	for v in ${REPLACING_VERSIONS}; do
		ver_test "${v}" -lt 1-r2 && FORCE_PRINT_ELOG=1
	done
	readme.gentoo_print_elog

	ewarn "Review config-protect updates and any custom /usr/local/libexec wrapper."
	ewarn "No existing service is automatically replaced, enabled or restarted."
}
