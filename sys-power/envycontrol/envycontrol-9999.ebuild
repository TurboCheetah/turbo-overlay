# Copyright 1999-2022 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

PYTHON_COMPAT=( python3_{7..10} )

inherit distutils-r1

DESCRIPTION="Easy GPU switching for Nvidia Optimus laptops under Linux"
HOMEPAGE="https://github.com/geminis3/envycontrol"

if [[ ${PV} == "9999" ]]; then
	inherit git-r3
	EGIT_REPO_URI="https://github.com/geminis3/${PN}.git"
	SRC_URI=""
else
	SRC_URI="https://github.com/geminis3/${PN}/archive/v${PV}.tar.gz -> ${P}.tar.gz"
	KEYWORDS="-* ~amd64 ~x86"
fi

LICENSE="MIT"
SLOT="0"

BDEPEND="dev-python/setuptools[${PYTHON_USEDEP}]"
RDEPEND=""
DEPEND="${REDEPEND}"
