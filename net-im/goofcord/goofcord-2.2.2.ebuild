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
REQUIRED_USE="elibc_glibc"

RESTRICT="strip"
QA_PREBUILT="opt/GoofCord/*"

RDEPEND="
	>=app-accessibility/at-spi2-core-2.46.0:2
	app-crypt/libsecret
	dev-libs/expat
	dev-libs/glib:2
	dev-libs/nspr
	dev-libs/nss
	dev-libs/wayland
	media-libs/alsa-lib
	media-libs/libglvnd
	media-libs/mesa
	net-print/cups
	sys-apps/dbus
	sys-apps/util-linux
	virtual/udev
	x11-libs/cairo
	x11-libs/gdk-pixbuf:2
	x11-libs/gtk+:3
	x11-libs/libdrm
	x11-libs/libnotify
	x11-libs/libX11
	x11-libs/libxcb
	x11-libs/libXcomposite
	x11-libs/libXcursor
	x11-libs/libXdamage
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXi
	x11-libs/libxkbcommon
	x11-libs/libXrandr
	x11-libs/libXrender
	x11-libs/libXScrnSaver
	x11-libs/libxshmfence
	x11-libs/libXtst
	x11-libs/pango
	x11-misc/xdg-utils
"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/GoofCord
	doins -r opt/GoofCord/*
	dosym -r /opt/GoofCord/goofcord /usr/bin/goofcord
	fperms 0755 \
		/opt/GoofCord/goofcord \
		/opt/GoofCord/chrome_crashpad_handler \
		/opt/GoofCord/resources/goofbind \
		/opt/GoofCord/resources/patchcord
	fperms 4755 /opt/GoofCord/chrome-sandbox

	domenu "${WORKDIR}/usr/share/applications/goofcord.desktop" || die "Failed to install .desktop file"
	insinto /usr/share/icons/hicolor
	doins -r usr/share/icons/hicolor/*
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
