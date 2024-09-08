EAPI=8

inherit unpacker desktop xdg-utils

PV="1.6.0"
DESCRIPTION="Take control of your Discord experience with GoofCord – a highly configurable and privacy minded discord client."
HOMEPAGE="https://github.com/Milkshiift/GoofCord"
SRC_URI="https://github.com/Milkshiift/GoofCord/releases/download/v${PV}/GoofCord-${PV}-linux-amd64.deb"

LICENSE="OSL-3.0"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND="x11-misc/xdg-utils"
DEPEND="${RDEPEND}"

S="${WORKDIR}"

src_unpack() {
	unpacker_src_unpack
}

src_install() {
	insinto /opt/GoofCord
	doins -r opt/GoofCord/*
	dosym /opt/GoofCord/goofcord /usr/bin/goofcord
	fperms 0755 /opt/GoofCord/goofcord

	domenu "${WORKDIR}/usr/share/applications/goofcord.desktop" || die "Failed to install .desktop file"
}

pkg_postinst() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}

pkg_postrm() {
	xdg_desktop_database_update
	xdg_mimeinfo_database_update
}
