# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit go-module

DESCRIPTION="Official Command Line Interface for the IPinfo API"
HOMEPAGE="https://github.com/ipinfo/cli"
SRC_URI="https://github.com/ipinfo/cli/archive/refs/tags/ipinfo-${PV}.tar.gz
	-> ${P}.gh.tar.gz"
S="${WORKDIR}/cli-ipinfo-${PV}"

LICENSE="Apache-2.0"
SLOT="0"
KEYWORDS="~amd64"

src_compile() {
	ego build -mod=vendor -trimpath -o "${T}/${PN}" ./ipinfo || die
}

src_install() {
	dobin "${T}/${PN}"
}
