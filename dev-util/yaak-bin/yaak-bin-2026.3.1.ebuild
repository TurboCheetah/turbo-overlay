# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker desktop xdg

DESCRIPTION="Desktop API client for REST, GraphQL, and gRPC"
HOMEPAGE="https://yaak.app https://github.com/mountain-loop/yaak"
SRC_URI="https://github.com/mountain-loop/yaak/releases/download/v${PV}/yaak_${PV}_amd64.deb"
S="${WORKDIR}"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

RESTRICT="mirror bindist strip"
QA_PREBUILT="
	usr/bin/yaak-app
	usr/lib/yaak/*
"

RDEPEND="
	net-libs/webkit-gtk:4.1
	x11-libs/gtk+:3
"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	dobin usr/bin/yaak-app
	dosym yaak-app /usr/bin/yaak

	insinto /usr/lib/yaak
	doins -r usr/lib/yaak/*

	domenu usr/share/applications/yaak.desktop || die
	insinto /usr/share/icons/hicolor
	doins -r usr/share/icons/hicolor/*

	insinto /usr/share/metainfo
	doins usr/share/metainfo/app.yaak.Yaak.metainfo.xml
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
