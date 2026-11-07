# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit acct-user

DESCRIPTION="User for the Rex terminal multiplexer shared server"

KEYWORDS="~amd64 ~arm64"

# Dynamic IDs: nothing is reserved for this overlay in baselayout's
# uid-gid.txt. Locked account with a nologin shell, like upstream's packages;
# the home directory doubles as Rex's state directory.
ACCT_USER_ID=-1
ACCT_USER_GROUPS=( rex )
ACCT_USER_HOME=/var/lib/rex
ACCT_USER_HOME_OWNER=rex:rex
ACCT_USER_HOME_PERMS=0700

acct-user_add_deps
