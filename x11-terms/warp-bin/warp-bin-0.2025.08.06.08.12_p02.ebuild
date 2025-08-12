# Copyright 1999-2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker desktop xdg

MY_PV="0.2025.08.06.08.12.stable_02"
DESCRIPTION="The Agentic Development Environment"
HOMEPAGE="https://www.warp.dev/"
SRC_URI="https://releases.warp.dev/stable/v${MY_PV}/warp-terminal-v${MY_PV}-1-x86_64.pkg.tar.zst"

S="${WORKDIR}"
LICENSE="all-rights-reserved"
SLOT="0"
KEYWORDS="~amd64"

RESTRICT="mirror bindist strip"

RDEPEND="
	net-misc/curl
	x11-themes/adwaita-icon-theme
	media-libs/fontconfig
	media-libs/libglvnd[X]
	x11-libs/libxkbcommon[X]
	media-libs/vulkan-loader
	x11-misc/xdg-utils
	x11-libs/libX11
	x11-libs/libxcb
	x11-libs/libXcursor
	x11-libs/libXi
	sys-libs/zlib
	virtual/opengl
"

QA_PREBUILT="opt/warpdotdev/*"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/warpdotdev
	doins -r opt/warpdotdev/*

	dobin usr/bin/warp-terminal

	fperms 0755 /opt/warpdotdev/warp-terminal/warp

	domenu usr/share/applications/dev.warp.Warp.desktop || die
	doicon -s 512 usr/share/icons/hicolor/512x512/apps/dev.warp.Warp.png || die
	doicon -s 256 usr/share/icons/hicolor/256x256/apps/dev.warp.Warp.png || die

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
