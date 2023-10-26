EAPI=8

inherit unpacker desktop xdg-utils

PV="4.4.17"
DESCRIPTION="BitTorrent streaming software for streaming anime torrents"
HOMEPAGE="https://github.com/ThaUnknown/miru"
SRC_URI="https://github.com/ThaUnknown/miru/releases/download/v${PV}/linux-Miru-${PV}.deb"

LICENSE="GPL-3"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND="x11-misc/xdg-utils"
DEPEND="${RDEPEND}"

S="${WORKDIR}"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/Miru
	doins -r opt/Miru/*
	dosym /opt/Miru/miru /usr/bin/miru
	fperms 0755 /opt/Miru/miru

	domenu "${WORKDIR}/usr/share/applications/miru.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}
