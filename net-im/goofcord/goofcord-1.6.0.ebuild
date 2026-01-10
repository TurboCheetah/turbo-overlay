# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker desktop xdg

DESCRIPTION="Highly configurable and privacy minded Discord client"
HOMEPAGE="https://github.com/Milkshiift/GoofCord"
SRC_URI="https://github.com/Milkshiift/GoofCord/releases/download/v${PV}/GoofCord-${PV}-linux-amd64.deb"
S="${WORKDIR}"

LICENSE="OSL-3.0"
SLOT="0"
KEYWORDS="~amd64"

RESTRICT="strip"
QA_PREBUILT="opt/GoofCord/*"

RDEPEND="x11-misc/xdg-utils"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/GoofCord
	doins -r opt/GoofCord/*
	dosym -r /opt/GoofCord/goofcord /usr/bin/goofcord
	fperms 0755 /opt/GoofCord/goofcord

	domenu "${WORKDIR}/usr/share/applications/goofcord.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
	xdg_icon_cache_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
	xdg_icon_cache_update
}
