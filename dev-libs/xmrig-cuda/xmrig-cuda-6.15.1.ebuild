# Copyright 1999-2021 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

CMAKE_IN_SOURCE_BUILD=1

inherit cmake

DESCRIPTION="CUDA Plugin for XMRig"
HOMEPAGE="https://xmrig.com https://github.com/xmrig/xmrig-cuda"

SRC_URI="https://github.com/xmrig/xmrig-cuda/archive/v${PV}.tar.gz -> ${P}.tar.gz"
KEYWORDS="~amd64"

LICENSE="GPL-3+"
SLOT="0"

DEPEND="dev-util/nvidia-cuda-toolkit"

PATCHES=(
	"${FILESDIR}"/${PN}-6.15.1-csddef.patch
)

pkg_setup() {
	if tc-is-clang; then
		eerror "Clang compilation is currently broken."
		die "Please use GCC"
	fi
}

src_install() {
	default
	dolib.so libxmrig-cuda.so
}
