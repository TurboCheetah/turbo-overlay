# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit pam readme.gentoo-r1 toolchain-funcs

# Upstream pin, from https://blob.superlogical.com/api/releases/rex/preview
# (the re-pin procedure is in AGENTS.md, "Rex re-pin").
MY_PV="${PV/_pre/-preview.}"
MY_COMMIT="1b6654811346488b696ded8f3c7e6701c57242f1"
MY_URI="https://blob.superlogical.com/download/rex/preview/${MY_COMMIT}"

DESCRIPTION="Rex terminal multiplexer by Superlogical (prebuilt server and CLI)"
HOMEPAGE="https://www.superlogical.com/rex"
SRC_URI="
	amd64? (
		elibc_musl? ( ${MY_URI}/rex-linux-amd64-musl.tar.gz -> ${P}-amd64-musl.tar.gz )
		!elibc_musl? ( ${MY_URI}/rex-linux-amd64.tar.gz -> ${P}-amd64.tar.gz )
	)
	arm64? (
		elibc_musl? ( ${MY_URI}/rex-linux-arm64-musl.tar.gz -> ${P}-arm64-musl.tar.gz )
		!elibc_musl? ( ${MY_URI}/rex-linux-arm64.tar.gz -> ${P}-arm64.tar.gz )
	)
"
S="${WORKDIR}"

LICENSE="all-rights-reserved"
SLOT="0"
KEYWORDS="~amd64 ~arm64"
IUSE="+pam"
RESTRICT="bindist mirror strip"
QA_PREBUILT="usr/bin/rex"

# libpam is dlopen()ed by the login server, so it is not a NEEDED entry.
RDEPEND="
	acct-group/rex
	acct-user/rex
	pam? ( sys-libs/pam )
"
# fowners in src_install resolves the account on the build host.
BDEPEND="
	acct-group/rex
	acct-user/rex
	sys-libs/ncurses
"
DEPEND="
	acct-group/rex
	acct-user/rex
"

DISABLE_AUTOFORMATTING=1
DOC_CONTENTS="Rex is installed, but no service is enabled yet.

Shared server (one server running as the rex account):

rc-update add rex default
rc-service rex start

Members of the rex group may use /run/rex/server.sock. Add yourself with
\"gpasswd -a <user> rex\" (log in again afterwards), then run:

rex -S /run/rex/server.sock ls
export REX_SERVER=/run/rex/server.sock    # make it the default

The rex account has /sbin/nologin as its shell and Rex starts every terminal
with its account's shell, so terminals on the shared server exit at once
until you give the account a real one. Put this in make.conf and re-emerge
acct-user/rex (a bare \"usermod --shell /bin/bash rex\" is undone the next time
that package is reinstalled):

ACCT_USER_REX_SHELL=\"/bin/bash\"

Command line, extra listeners (SSH forward, Tailscale Serve, ...) are set in
/etc/conf.d/rex; the log is /var/log/rex/server.log. For the login server
(one server per account, log in /var/log/rex-login-server.log; with USE=pam
it opens a PAM session per login, with USE=-pam it only does shadow-based
checks and no login session) edit /etc/rex/accounts.jsonc, which admits
nobody as shipped, and enable
rex-login-server instead of rex: the two cannot run together. Stopping
rex-login-server leaves the per-account servers it started running, and rex
refuses to start while /run/rex still holds their directories, so stop those
servers or reboot before switching back.

xterm-rex is installed system-wide, but each account still needs
\"rex terminfo setup\" once. It also writes an ssh fallback, which only works
with OpenSSH 7.8 or newer and makes every ssh command fail on older versions;
that is why this package does not install /etc/ssh/ssh_config.d/50-rex.conf.

A running server keeps the old binary after an upgrade. Restarting it with
\"rc-service rex restart\" ends every session on it. That is also how to
recover from a crash (\"restart\", not \"start\"); only root sees the
\"crashed\" state, non-root \"rc-service rex status\" keeps saying \"started\".

Do not also run upstream's \"install.sh --tarball\": it installs
/usr/local/bin/rex, which normally comes before /usr/bin in PATH and would
shadow this package."

src_compile() {
	# The terminfo source only comes from the binary, which cannot run when
	# the target is not the build machine.
	if tc-is-cross-compiler; then
		ewarn "Cross-compiling: the xterm-rex terminfo entry will not be installed"
		return
	fi

	local reported
	reported=$(./rex version) || die "./rex version failed"
	[[ ${reported%%$'\n'*} == "rex ${MY_PV}" ]] ||
		die "unexpected version \"${reported%%$'\n'*}\", expected \"rex ${MY_PV}\""
	[[ ${reported} == *"commit: ${MY_COMMIT}"* ]] ||
		die "binary was not built from ${MY_COMMIT}"

	./rex terminfo show > xterm-rex.terminfo || die "./rex terminfo show failed"
}

src_install() {
	dobin rex

	if [[ -s xterm-rex.terminfo ]]; then
		dodir /usr/share/terminfo
		tic -x -o "${ED}/usr/share/terminfo" xterm-rex.terminfo || die "tic failed"
	fi

	newinitd "${FILESDIR}/rex.initd" rex
	newconfd "${FILESDIR}/rex.confd" rex
	newinitd "${FILESDIR}/rex-login-server.initd" rex-login-server
	newconfd "${FILESDIR}/rex-login-server.confd" rex-login-server

	insinto /etc/logrotate.d
	newins "${FILESDIR}/rex.logrotate" rex

	insinto /etc/rex
	doins "${FILESDIR}/accounts.jsonc"

	if use pam; then
		newpamd "${FILESDIR}/rex-login-server.pam" rex-login-server
	else
		# No libpam to load: the login server does its own shadow checks.
		# The init script carries the same command line as a fallback for
		# a removed conf.d file.
		sed -i -e 's/--pam-service rex-login-server/& --pam=false/' \
			"${ED}/etc/conf.d/rex-login-server" \
			"${ED}/etc/init.d/rex-login-server" || die
	fi

	# State directory (also the rex account's home) and log directory.
	keepdir /var/lib/rex /var/log/rex
	fowners rex:rex /var/lib/rex /var/log/rex
	fperms 0700 /var/lib/rex
	fperms 0750 /var/log/rex

	readme.gentoo_create_doc
}

pkg_postinst() {
	readme.gentoo_print_elog

	# /etc/conf.d is config-protected, so an existing file keeps the
	# command line it was installed with and the sed in src_install only
	# reaches new installs.
	if ! use pam && [[ -f ${EROOT}/etc/conf.d/rex-login-server ]] &&
		! grep -q -e '--pam=false' "${EROOT}/etc/conf.d/rex-login-server"
	then
		ewarn "USE=-pam, but ${EROOT}/etc/conf.d/rex-login-server does not pass"
		ewarn "--pam=false, so the login server would still ask for PAM."
		ewarn "Add --pam=false to its command_args (or merge the new file with"
		ewarn "dispatch-conf/etc-update) before starting rex-login-server."
	fi
}

pkg_prerm() {
	if [[ -z ${REPLACED_BY_VERSION} ]] &&
		[[ -S ${EROOT}/run/rex/server.sock || -S ${EROOT}/run/rex/login-server.sock ]]
	then
		ewarn "A Rex server is still running. Stop it with \"rc-service rex stop\""
		ewarn "(or rex-login-server) before the init scripts disappear."
	fi
}
