# Copyright 1999-2024 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker desktop xdg

DESCRIPTION="Performance of web Discord with comfort of Discord Desktop"
HOMEPAGE="https://github.com/Vencord/Vesktop"
SRC_URI="https://github.com/Vencord/Vesktop/releases/download/v${PV}/vesktop_${PV}_amd64.deb"
S="${WORKDIR}"

LICENSE="GPL-3"
SLOT="0"

KEYWORDS="~amd64"

RDEPEND="x11-misc/xdg-utils x11-libs/libnotify"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/Vesktop
	doins -r opt/Vesktop/*
	dosym ../opt/Vesktop/vesktop /usr/bin/vesktop
	fperms 0755 /opt/Vesktop/vesktop

	domenu "${WORKDIR}/usr/share/applications/vesktop.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}
