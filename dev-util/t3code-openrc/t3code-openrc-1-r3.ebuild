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
	# readme.gentoo_print_elog is silent on upgrades, and -1 and -1-r1 installed
	# the documentation without ever printing it. -1-r2 printed it on a fresh
	# install only, so a host that reached -1-r2 by upgrading from -1 or -1-r1
	# never saw it either. Print it once when replacing any of those.
	#
	# Accepted trade: a host that installed -1-r2 fresh already saw the doc and
	# will see it once more on its move to -1-r3. That duplicate is deliberate,
	# because the ebuild cannot tell the two -1-r2 cohorts apart and a silent gap
	# is worse than a repeat. Upgrading from -1-r3 or later stays quiet.
	local v
	for v in ${REPLACING_VERSIONS}; do
		ver_test "${v}" -le 1-r2 && FORCE_PRINT_ELOG=1
	done
	readme.gentoo_print_elog

	ewarn "Review config-protect updates and any custom /usr/local/libexec wrapper."
	ewarn "No existing service is automatically replaced, enabled or restarted."
}
