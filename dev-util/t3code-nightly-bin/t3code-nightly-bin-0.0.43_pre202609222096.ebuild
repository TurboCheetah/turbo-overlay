# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

# Raw upstream nightly tag, e.g. 0.0.37-nightly.20260830.1227.
# PV is the Gentoo-form date+time snapshot (0.0.37_preYYYYMMDDHHMM).
MY_PV="0.0.43-nightly.20260922.2096"
APPIMAGE_NAME="T3-Code-${MY_PV}-x86_64.AppImage"

inherit desktop xdg

DESCRIPTION="Desktop app for working with code and AI coding agents (nightly build)"
HOMEPAGE="https://t3.codes https://github.com/pingdotgg/t3code"
SRC_URI="https://github.com/pingdotgg/t3code/releases/download/v${MY_PV}/${APPIMAGE_NAME}"

S="${WORKDIR}/squashfs-root"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"
REQUIRED_USE="elibc_glibc"
RESTRICT="strip"
QA_PREBUILT="opt/${PN}/*"

RDEPEND="
	!dev-util/t3code-bin
	app-accessibility/at-spi2-core:2
	dev-libs/expat
	dev-libs/glib:2
	dev-libs/nspr
	dev-libs/nss
	media-libs/alsa-lib
	media-libs/mesa
	net-print/cups
	sys-apps/dbus
	sys-fs/fuse:0
	virtual/udev
	virtual/zlib
	x11-libs/cairo
	x11-libs/gdk-pixbuf:2
	x11-libs/gtk+:3
	x11-libs/libX11
	x11-libs/libXcomposite
	x11-libs/libXdamage
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXrandr
	x11-libs/libdrm
	x11-libs/libxcb
	x11-libs/libxkbcommon
	x11-libs/pango
	x11-misc/xdg-utils
"

src_unpack() {
	cp "${DISTDIR}/${APPIMAGE_NAME}" "${WORKDIR}/${APPIMAGE_NAME}" || die
	chmod +x "${WORKDIR}/${APPIMAGE_NAME}" || die
	"${WORKDIR}/${APPIMAGE_NAME}" --appimage-extract >/dev/null || die
}

t3code_install_icon() {
	local icon

	icon=$(find "${S}" -path '*/resources/icon.png' -print -quit)
	[[ -n ${icon} ]] || icon=$(find "${S}" -name 't3code.png' -print -quit)
	[[ -n ${icon} ]] || die "Could not locate application icon in ${APPIMAGE_NAME}"

	newicon -s 512 "${icon}" t3code.png
}

t3code_install_license() {
	local license

	license=$(find "${S}" -maxdepth 2 -name 'LICENSE' -print -quit)
	[[ -n ${license} ]] || die "Could not locate LICENSE in ${APPIMAGE_NAME}"

	insinto /usr/share/licenses/${PN}
	newins "${license}" LICENSE
}

src_install() {
	dodir /opt/${PN}
	cp -a "${S}"/. "${ED}/opt/${PN}/" || die
	chmod -R a+rX "${ED}/opt/${PN}" || die

	cat > "${T}/t3code" <<'EOF' || die
#!/usr/bin/env bash
set -euo pipefail

appdir='/opt/t3code-nightly-bin'
export APPDIR="${appdir}"

if [[ -z "${CODEX_CLI_PATH-}" ]] && command -v codex >/dev/null 2>&1; then
	export CODEX_CLI_PATH="$(command -v codex)"
fi

export PATH="${appdir}:${appdir}/usr/sbin:${PATH}"
export XDG_DATA_DIRS="${appdir}/usr/share${XDG_DATA_DIRS:+:${XDG_DATA_DIRS}}"
export GSETTINGS_SCHEMA_DIR="${appdir}/usr/share/glib-2.0/schemas${GSETTINGS_SCHEMA_DIR:+:${GSETTINGS_SCHEMA_DIR}}"

extra_flags=()
if [[ -n "${WAYLAND_DISPLAY-}" || "${XDG_SESSION_TYPE-}" == "wayland" ]]; then
	extra_flags+=(--enable-features=UseOzonePlatform --ozone-platform=wayland --ozone-platform-hint=wayland)
else
	extra_flags+=(--ozone-platform-hint=auto)
fi

exec "${appdir}/t3code" --no-sandbox "${extra_flags[@]}" "$@"
EOF

	dobin "${T}/t3code"
	dosym t3code /usr/bin/t3-code-desktop

	t3code_install_icon

	cat > "${T}/t3code.desktop" <<'EOF' || die
[Desktop Entry]
Name=T3 Code (Nightly)
Comment=T3 Code desktop build
Exec=t3code %U
Terminal=false
Type=Application
Icon=t3code
StartupWMClass=t3code
MimeType=x-scheme-handler/t3code;x-scheme-handler/t3code-dev;
Categories=Development;
EOF
	domenu "${T}/t3code.desktop" || die

	t3code_install_license
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
