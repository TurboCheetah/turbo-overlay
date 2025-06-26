# Copyright 1999-2024 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker desktop xdg

DESCRIPTION="BitTorrent streaming software for streaming anime torrents"
HOMEPAGE="https://github.com/ThaUnknown/miru"
SRC_URI="https://github.com/ThaUnknown/miru/releases/download/v${PV}/linux-hayase-${PV}-linux.deb"
S="${WORKDIR}"

LICENSE="GPL-3"
SLOT="0"

KEYWORDS="~amd64"

RDEPEND="x11-misc/xdg-utils"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/Hayase
	doins -r opt/Hayase/*
	dosym ../opt/Hayase/hayase /usr/bin/hayase
	fperms 0755 /opt/Hayase/hayase

	domenu "${WORKDIR}/usr/share/applications/hayase.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}
