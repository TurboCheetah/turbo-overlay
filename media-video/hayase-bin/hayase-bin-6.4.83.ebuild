# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

CHROMIUM_LANGS="
	af am ar bg bn ca cs da de el en-GB en-US es es-419 et fa fi fil fr gu he hi
	hr hu id it ja kn ko lt lv ml mr ms nb nl pl pt-BR pt-PT ro ru sk sl sr sv
	sw ta te th tr uk ur vi zh-CN zh-TW
"

inherit chromium-2 desktop linux-info unpacker xdg

DESCRIPTION="BitTorrent streaming software for streaming anime torrents"
HOMEPAGE="https://hayase.watch/"
SRC_URI="https://api.hayase.watch/files/linux-hayase-${PV}-linux.deb"
S="${WORKDIR}"

LICENSE="GPL-3"
SLOT="0"
KEYWORDS="~amd64"
REQUIRED_USE="elibc_glibc"

RESTRICT="bindist mirror strip test"

RDEPEND="
	>=app-accessibility/at-spi2-core-2.46.0:2
	app-crypt/libsecret
	dev-libs/expat
	dev-libs/glib:2
	dev-libs/nspr
	dev-libs/nss
	dev-libs/wayland
	media-libs/alsa-lib
	media-libs/fontconfig
	media-libs/mesa[gbm(+)]
	net-print/cups
	sys-apps/dbus
	sys-apps/util-linux
	sys-libs/glibc
	x11-libs/cairo
	x11-libs/libdrm
	x11-libs/gdk-pixbuf:2
	x11-libs/gtk+:3
	x11-libs/libX11
	x11-libs/libXcomposite
	x11-libs/libXdamage
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXrandr
	x11-libs/libxcb
	x11-libs/libxkbcommon
	x11-libs/pango
	x11-misc/xdg-utils
"

DESTDIR="/opt/Hayase"

QA_PREBUILT="*"

CONFIG_CHECK="~USER_NS"

src_unpack() {
	unpacker_src_unpack
}

src_prepare() {
	pushd "opt/Hayase/locales" >/dev/null || die
	chromium_remove_language_paks
	popd >/dev/null || die
}

src_configure() {
	chromium_suid_sandbox_check_kernel_config
}

src_install() {
	insinto "${DESTDIR}"
	doins -r opt/Hayase/*
	dosym -r "${DESTDIR}/hayase" /usr/bin/hayase

	fperms 0755 \
		"${DESTDIR}/hayase" \
		"${DESTDIR}/chrome_crashpad_handler"

	# Match GURU vesktop-bin / gentoo discord: setuid sandbox via 4711.
	fowners root "${DESTDIR}/chrome-sandbox"
	fperms 4711 "${DESTDIR}/chrome-sandbox"

	domenu "${WORKDIR}/usr/share/applications/hayase.desktop" || die
	insinto /usr/share/icons/hicolor
	doins -r usr/share/icons/hicolor/*
}

pkg_postinst() {
	xdg_pkg_postinst
}

pkg_postrm() {
	xdg_pkg_postrm
}
