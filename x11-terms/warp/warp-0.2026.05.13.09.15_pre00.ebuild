# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop xdg

MY_PV="0.2026.05.13.09.15.preview_00"
DESCRIPTION="The Agentic Development Environment"
HOMEPAGE="https://www.warp.dev/ https://github.com/warpdotdev/Warp"
SRC_URI="https://github.com/warpdotdev/Warp/archive/refs/tags/v${MY_PV}.tar.gz -> ${P}.tar.gz"
S="${WORKDIR}/warp-${MY_PV}"

LICENSE="AGPL-3 MIT"
SLOT="0"
KEYWORDS="~amd64"

RESTRICT="network-sandbox"

RDEPEND="
	app-arch/brotli
	dev-libs/expat
	dev-libs/libgit2
	dev-libs/openssl
	dev-libs/wayland
	media-libs/alsa-lib
	media-libs/fontconfig
	media-libs/freetype
	media-libs/mesa[vulkan,wayland]
	virtual/zlib:0/1
	x11-libs/libX11
	x11-libs/libXi
	x11-libs/libXcursor
	x11-libs/libxcb
	x11-libs/libxkbcommon[X]
"
DEPEND="${RDEPEND}"
BDEPEND="
	app-misc/jq
	dev-build/cmake
	dev-lang/rust
	dev-libs/protobuf
	llvm-core/clang
	virtual/pkgconfig
"

src_compile() {
	cargo build --release || die "cargo build failed"
}

src_install() {
	dobin target/release/warp

	domenu app/channels/stable/dev.warp.Warp.desktop || die

	doicon -s 512 app/channels/stable/icon/no-padding/512x512.png || die
	doicon -s 256 app/channels/stable/icon/no-padding/256x256.png || die
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
