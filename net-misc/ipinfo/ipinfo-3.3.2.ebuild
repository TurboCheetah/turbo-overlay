# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="Official Command Line Interface for the IPinfo API"
HOMEPAGE="https://github.com/ipinfo/cli"
SRC_URI="https://github.com/ipinfo/cli/archive/refs/tags/ipinfo-${PV}.tar.gz
	-> ${P}.gh.tar.gz"
S="${WORKDIR}/cli-ipinfo-${PV}"

LICENSE="Apache-2.0"
SLOT="0"
KEYWORDS="~amd64"

BDEPEND=">=dev-lang/go-1.22"

src_compile() {
	CGO_ENABLED=0 go build -mod=vendor -trimpath -o "${T}/${PN}" ./ipinfo || die
}

src_install() {
	dobin "${T}/${PN}"
}
