# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop optfeature systemd unpacker xdg

DESCRIPTION="Local voice dictation: hold a key, speak, and it types"
HOMEPAGE="
	https://anoma.ly/hex/
	https://github.com/anomalyco/hex
"
SRC_URI="https://github.com/anomalyco/hex/releases/download/v${PV}/hex-${PV}-linux-x64.deb"
S="${WORKDIR}"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"
REQUIRED_USE="elibc_glibc"
RESTRICT="bindist mirror strip test"

# Linked: alsa-lib, vulkan-loader, libxkbcommon, openssl.
# Loaded at runtime: the X11, xcb, Xcursor, Xi and Wayland client libraries.
# curl downloads speech models.
# Upstream builds on Ubuntu 24.04 and documents glibc 2.39 as the floor
# (docs/linux.md); the binary references GLIBC_2.39 symbols.
RDEPEND="
	>=sys-libs/glibc-2.39
	dev-libs/openssl:0/3
	dev-libs/wayland
	media-libs/alsa-lib
	media-libs/vulkan-loader
	net-misc/curl
	x11-libs/libX11
	x11-libs/libXcursor
	x11-libs/libXi
	x11-libs/libxcb
	x11-libs/libxkbcommon[X]
"

QA_PREBUILT="usr/bin/hex"

src_install() {
	dobin usr/bin/hex
	insinto /usr/share/licenses/${PN}
	doins usr/share/licenses/hex/LICENSE

	domenu usr/share/applications/anomaly.hex.desktop
	local size
	for size in 16 24 32 48 64 128 256 512; do
		doicon -s ${size} usr/share/icons/hicolor/${size}x${size}/apps/anomaly.hex.png
	done

	systemd_douserunit usr/lib/systemd/user/hex.service
}

pkg_postinst() {
	xdg_pkg_postinst

	optfeature "pasting on Wayland" gui-apps/wl-clipboard
	optfeature "GPU transcription" media-libs/mesa[vulkan]
	optfeature "muting output while dictating" media-video/wireplumber media-sound/pulseaudio
	optfeature "opening links from voice commands" x11-misc/xdg-utils

	if [[ -z ${REPLACING_VERSIONS} ]]; then
		elog "Run 'hex' or open Hex from your launcher; the first run downloads a"
		elog "speech model. x86_64 requires a CPU with AVX2 and a Vulkan driver."
		elog "On wlroots compositors, paste also needs gui-apps/wtype (GURU)."
		elog "Start Hex at login with: systemctl --user enable hex.service"
		elog "Updates come from Portage; Hex does not replace this package itself."
	fi
}
