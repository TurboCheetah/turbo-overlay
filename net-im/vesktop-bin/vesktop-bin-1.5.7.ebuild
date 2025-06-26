# Copyright 1999-2024 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="DEPRECATED: Use net-im/vesktop from the GURU overlay instead"
HOMEPAGE="https://github.com/Vencord/Vesktop"

LICENSE="metapackage"
SLOT="0"
KEYWORDS="~amd64"

pkg_setup() {
	ewarn
	ewarn "This package has been deprecated and moved to the GURU overlay."
	ewarn "Please install net-im/vesktop from the GURU overlay instead:"
	ewarn
	ewarn "  1. Add the GURU overlay: eselect repository enable guru"
	ewarn "  2. Sync: emerge --sync guru"
	ewarn "  3. Install: emerge net-im/vesktop"
	ewarn "  4. Uninstall this package: emerge --unmerge net-im/vesktop-bin"
	ewarn
	die "Package deprecated - use net-im/vesktop from GURU overlay"
}
