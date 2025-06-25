EAPI=8

inherit unpacker desktop xdg-utils

PV="6.4.0"
DESCRIPTION="BitTorrent streaming software for streaming anime torrents"
HOMEPAGE="https://hayase.watch/"
SRC_URI="https://github.com/hayase-app/ui/releases/download/v${PV}/linux-hayase-${PV}-linux.deb"

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
	insinto /opt/Hayase
	doins -r opt/Hayase/*
	dosym /opt/Hayase/hayase /usr/bin/hayase
	fperms 0755 /opt/Hayase/hayase

	domenu "${WORKDIR}/usr/share/applications/hayase.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}
