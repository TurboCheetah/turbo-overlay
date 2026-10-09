"""Acceptance tests at the public create-ebuild main/launcher boundaries."""

from contextlib import suppress
from pathlib import Path

import pytest


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    root = tmp_path / "overlay"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles/repo_name").write_text("test-overlay\n", encoding="utf-8")
    (root / "metadata").mkdir()
    (root / "metadata/layout.conf").write_text("masters = gentoo\n", encoding="utf-8")
    return root


def arguments(root: Path, *extra: str) -> list[str]:
    return [
        "dev-util/example-bin",
        "--overlay-path",
        str(root),
        "--version",
        "1.2.3",
        "--template",
        "binary-direct",
        "--upstream-url",
        "https://downloads.example.org/example-1.2.3.tar.gz",
        "--license",
        "MIT",
        "--description",
        "Example binary tool",
        "--homepage",
        "https://example.org/",
        "--maintainer-email",
        "owner@example.org",
        *extra,
    ]


def invoke(args: list[str]) -> int:
    from overlay_tools.cli.create_ebuild import main

    return main(args)


def test_entropy_failure_leaves_no_open_descriptors_or_artifacts(overlay, capsys, monkeypatch):
    import os
    import secrets

    from overlay_tools.cli.create_ebuild import main

    # Count invocation-owned FDs, not ctypes' retained libffi FD on Python 3.14.
    before = set(os.listdir("/proc/self/fd"))

    def unavailable_entropy(_size):
        raise OSError("entropy source unavailable")

    monkeypatch.setattr(secrets, "token_hex", unavailable_entropy)
    assert main(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert "entropy source unavailable" in captured.err
    assert not captured.out
    assert not (overlay / "dev-util").exists()
    after = set(os.listdir("/proc/self/fd"))
    leaked = after - before
    try:
        assert not leaked
    finally:
        for descriptor in leaked:
            with suppress(OSError):
                os.close(int(descriptor))


def test_default_preview_renders_both_artifacts_without_creating_category(overlay, capsys):
    assert invoke(arguments(overlay)) == 0
    output = capsys.readouterr().out
    assert "Preview only" in output
    assert "dev-util/example-bin/example-bin-1.2.3.ebuild" in output
    assert "DESCRIPTION='Example binary tool'" in output
    assert "KEYWORDS='~amd64'" in output
    assert "newbin \"${S}/example\" 'example'" in output
    assert "<email>owner@example.org</email>" in output
    assert "Manifest" in output
    assert "pkgcheck scan -f latest dev-util/example-bin" in output
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize(
    "email",
    ["first.last@example.org", "owner+tag@example.org", "o'brien@example.org"],
)
def test_dotted_atext_local_parts_are_accepted(overlay, capsys, email):
    args = arguments(overlay)
    args[args.index("--maintainer-email") + 1] = email
    assert invoke(args) == 0
    assert f"<email>{email}</email>" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/latest",
        "https://example.org/download?version=1.2.3",
        "https://cdn.example.org/sha256/abcdef",
    ],
)
def test_offline_creator_requires_manual_immutability_and_archive_layout_review(
    overlay, capsys, mode, url
):
    assert invoke(arguments(overlay, mode, "--upstream-url", url)) == 0
    output = capsys.readouterr().out
    assert (
        "Review an immutable release-specific upstream URL; syntax cannot prove immutability."
        in output
    )
    assert f'SRC_URI="{url} -> ${{P}}.bin"' in output
    assert (
        '# TODO: for archives, set S to the actual upstream extraction directory.\nS="${WORKDIR}"'
        in output
    )
    if mode == "--dry-run":
        assert not (overlay / "dev-util").exists()


def test_creator_readme_lists_required_inputs_and_guide_example_previews_without_writes(
    overlay, tmp_path
):
    import shlex
    import subprocess

    tools = Path(__file__).resolve().parents[1]
    readme = (tools / "README.md").read_text()
    for required in [
        "category/package",
        "--overlay-path",
        "--version",
        "--template",
        "--upstream-url",
        "--license",
        "--description",
        "--homepage",
        "--maintainer-email",
    ]:
        assert f"`{required}`" in readme
    guide = (tools / "docs/create-ebuild.md").read_text()
    block = next(
        part
        for part in guide.split("```bash\n")[1:]
        if part.startswith(".agents/skills/overlay-tools/bin/create-ebuild")
    )
    command = shlex.split(block.split("```", 1)[0].replace("\\\n", ""))
    command[0] = str(tools / "bin/create-ebuild")
    command[command.index("--overlay-path") + 1] = str(overlay)
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Preview only" in result.stdout
    assert sorted(path.name for path in overlay.iterdir()) == ["metadata", "profiles"]


@pytest.mark.parametrize("keywords", ["", "~arm64", "~amd64 ~arm64"])
def test_keyword_override_is_literal_and_not_host_detected(overlay, capsys, keywords):
    assert invoke(arguments(overlay, "--keywords", keywords)) == 0
    assert f"KEYWORDS='{keywords}'" in capsys.readouterr().out
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("atom", "../example-bin"),
        ("atom", "dev-util/example-bin-1.2"),
        ("atom", "=dev-util/example-bin"),
        ("--version", "1.2;touch injected"),
        ("--version", "v1.2"),
        ("--license", "MIT$(touch injected)"),
        ("--description", "$(touch injected)"),
        ("--description", "x'; touch injected; #"),
        ("--description", 'x"; touch injected; #'),
        ("--maintainer-name", "Name | id"),
        ("--description", "two\nlines"),
        ("--description", ""),
        ("--upstream-url", "http://example.org/file"),
        ("--upstream-url", "https://example.org/$(touch-injected)"),
        ("--upstream-url", "https://owner:secret@example.org/file"),
        ("--upstream-url", "https://example.org/file#fragment"),
        ("--upstream-url", "https://EXAMPLE.org/file"),
        ("--upstream-url", "https://example.org/%2e%2e/file"),
        ("--upstream-url", "https://example.org/file%0a"),
        ("--homepage", "https://example.org/`id`"),
        ("--maintainer-email", "bad-email"),
        ("--maintainer-email", "owner@example.org\n<name>injected</name>"),
        ("--maintainer-email", "owner..name@example.org"),
        ("--maintainer-email", "owner.@example.org"),
        ("--maintainer-email", ".owner@example.org"),
        ("--maintainer-name", "$(id)"),
        ("--binary-name", "../outside"),
        ("--binary-name", "--help"),
        ("--keywords", "~amd64; id"),
    ],
)
def test_invalid_input_is_rejected_before_any_artifact(overlay, capsys, field, value):
    args = arguments(overlay, "--write")
    if field == "atom":
        args[0] = value
    elif field in args:
        args[args.index(field) + 1] = value
    else:
        args.extend([field, value])
    try:
        result = invoke(args)
    except SystemExit as exc:
        result = exc.code
    assert result != 0
    assert capsys.readouterr().err
    assert sorted(path.name for path in overlay.iterdir()) == ["metadata", "profiles"]


@pytest.mark.parametrize("root_kind", ["missing", "empty", "subdir"])
def test_requires_explicit_existing_overlay_root(overlay, root_kind, capsys):
    root = overlay / root_kind
    if root_kind != "missing":
        root.mkdir()
    assert invoke(arguments(root, "--write")) == 1
    assert capsys.readouterr().err
    assert not (root / "dev-util").exists()


def test_explicit_write_creates_only_reviewable_ebuild_and_metadata(overlay, capsys):
    assert invoke(arguments(overlay, "--write", "--keywords", "~amd64")) == 0
    package = overlay / "dev-util/example-bin"
    assert sorted(path.name for path in package.iterdir()) == [
        "example-bin-1.2.3.ebuild",
        "metadata.xml",
    ]
    text = (package / "example-bin-1.2.3.ebuild").read_text()
    assert "EAPI=8" in text
    assert "KEYWORDS='~amd64'" in text
    assert "QA_PREBUILT='usr/bin/example'" in text
    assert 'RESTRICT="mirror strip"' in text
    assert "Written starter only" in capsys.readouterr().out
    assert sorted(path.name for path in (overlay / "dev-util").iterdir()) == ["example-bin"]


@pytest.mark.parametrize("artifact", [None, "Manifest", "metadata.xml", "example-bin-1.2.3.ebuild"])
@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
def test_existing_package_is_untouched_even_when_empty(overlay, artifact, mode):
    package = overlay / "dev-util/example-bin"
    package.mkdir(parents=True)
    if artifact:
        (package / artifact).write_bytes(b"original\x00\xff")
    assert invoke(arguments(overlay, mode)) == 1
    assert sorted(path.name for path in package.iterdir()) == ([artifact] if artifact else [])
    if artifact:
        assert (package / artifact).read_bytes() == b"original\x00\xff"


@pytest.mark.parametrize("location", ["category", "package", "root", "ancestor"])
def test_symlink_targets_or_parents_cannot_escape(overlay, tmp_path, location):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = overlay
    if location == "category":
        (overlay / "dev-util").symlink_to(outside, target_is_directory=True)
    elif location == "package":
        (overlay / "dev-util").mkdir()
        (overlay / "dev-util/example-bin").symlink_to(outside, target_is_directory=True)
    elif location == "root":
        root = tmp_path / "linked-overlay"
        root.symlink_to(overlay, target_is_directory=True)
    else:
        link = tmp_path / "linked-parent"
        link.symlink_to(tmp_path, target_is_directory=True)
        root = link / "overlay"
    assert invoke(arguments(root, "--write")) == 1
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize(
    "url,suffix",
    [
        ("https://example.org/tool.tar.gz?token=abc", ".tar.gz"),
        ("https://example.org/tool.tar.gz?filename=ignored.zip&file=ignored", ".tar.gz"),
        ("https://example.org/download?file=tool.tar.gz", ".tar.gz"),
        ("https://example.org/download?filename=tool.tar.xz&token=abc", ".tar.xz"),
        ("https://example.org/download?token=abc&file=tool%2Etar%2Ebz2", ".tar.bz2"),
        ("https://example.org/download?file=tool.tgz&filename=tool.tgz", ".tgz"),
        ("https://example.org/download?filename=tool.zip", ".zip"),
        ("https://example.org/tool?token=archive.tar.gz", ".bin"),
        ("https://example.org/tool?filename=tool", ".bin"),
    ],
)
def test_direct_archive_query_hints_preserve_literal_url_without_fetching(
    overlay, capsys, monkeypatch, mode, url, suffix
):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network or process execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert invoke(arguments(overlay, mode, "--upstream-url", url)) == 0
    output = capsys.readouterr().out
    assert f'SRC_URI="{url} -> ${{P}}{suffix}"' in output
    assert ("src_unpack() { :; }" in output) == (suffix == ".bin")
    if mode == "--write":
        text = (overlay / "dev-util/example-bin/example-bin-1.2.3.ebuild").read_text()
        assert f'SRC_URI="{url} -> ${{P}}{suffix}"' in text
        assert ("src_unpack() { :; }" in text) == (suffix == ".bin")
    else:
        assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize(
    "query",
    ["file=tool.tar.gz&filename=tool.zip", "file=tool&filename=tool.tar.gz"],
)
def test_conflicting_direct_archive_query_hints_fail_before_writes(overlay, capsys, mode, query):
    assert (
        invoke(arguments(overlay, mode, "--upstream-url", f"https://example.org/download?{query}"))
        == 1
    )
    captured = capsys.readouterr()
    assert "conflicting archive filename hints" in captured.err
    assert not captured.out
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize(
    "template,url,required",
    [
        ("binary-direct", "https://example.org/tool", 'newbin "${DISTDIR}/${P}.bin"'),
        ("binary-direct", "https://example.org/tool.zip", "${P}.zip"),
        ("binary-deb", "https://example.org/tool.deb", "inherit unpacker"),
        ("binary-appimage-intact", "https://example.org/tool.AppImage", "sys-fs/fuse:0"),
    ],
)
def test_templates_generate_only_the_selected_binary_strategy(
    overlay, capsys, template, url, required
):
    args = arguments(overlay, "--write", "--binary-name", "custom-tool")
    args[args.index("--template") + 1] = template
    args[args.index("--upstream-url") + 1] = url
    assert invoke(args) == 0
    text = (overlay / "dev-util/example-bin/example-bin-1.2.3.ebuild").read_text()
    assert required in text
    assert "QA_PREBUILT='usr/bin/custom-tool'" in text
    assert "@@" not in text
    assert "chromium-2" not in text
    assert "--appimage-extract" not in text
    if template == "binary-deb":
        assert "unpacker_src_unpack" in text
        assert 'newbin "${S}/usr/bin/custom-tool"' in text
    if template == "binary-appimage-intact":
        assert "src_unpack() { :; }" in text
        assert 'newbin "${DISTDIR}/${P}.AppImage"' in text
    assert "--expect usr/bin/custom-tool" in capsys.readouterr().out
    import subprocess

    syntax = subprocess.run(
        ["bash", "-n", str(overlay / "dev-util/example-bin/example-bin-1.2.3.ebuild")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_metadata_escapes_names_and_ebuild_quotes_apostrophes(overlay):
    import subprocess
    import xml.etree.ElementTree as ET

    assert (
        invoke(
            arguments(
                overlay,
                "--write",
                "--maintainer-name",
                "O'Brian & <Team>",
                "--description",
                "Owner's binary tool",
            )
        )
        == 0
    )
    package = overlay / "dev-util/example-bin"
    metadata = ET.parse(package / "metadata.xml")
    assert metadata.findtext("maintainer/name") == "O'Brian & <Team>"
    assert metadata.findtext("maintainer/email") == "owner@example.org"
    text = (package / "metadata.xml").read_text()
    assert "&amp;" in text and "&lt;Team&gt;" in text
    # Parse only. Never source an ebuild or execute its phases/vendor payload.
    result = subprocess.run(
        ["bash", "-n", str(package / "example-bin-1.2.3.ebuild")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "missing",
    [
        "--overlay-path",
        "--version",
        "--template",
        "--upstream-url",
        "--license",
        "--description",
        "--homepage",
        "--maintainer-email",
    ],
)
def test_required_fields_never_fall_back_to_host_identity(overlay, monkeypatch, missing):
    monkeypatch.setenv("EMAIL", "host-owner@example.org")
    monkeypatch.setenv("USER", "host-user")
    args = arguments(overlay, "--write")
    index = args.index(missing)
    del args[index : index + 2]
    with pytest.raises(SystemExit) as exc:
        invoke(args)
    assert exc.value.code == 2
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("extra", [["--write", "--dry-run"], ["--eapi", "7"]])
def test_unsupported_or_conflicting_modes_fail_before_writes(overlay, extra):
    with pytest.raises(SystemExit) as exc:
        invoke(arguments(overlay, *extra))
    assert exc.value.code == 2
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
def test_no_network_subprocess_or_ebuild_execution(overlay, monkeypatch, mode):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network or process execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert invoke(arguments(overlay, mode)) == 0


@pytest.mark.parametrize("failure", ["open", "fsync"])
@pytest.mark.parametrize("existing_category", [False, True])
def test_io_failure_removes_only_own_partial_outputs(
    overlay, monkeypatch, capsys, failure, existing_category
):
    import os

    category = overlay / "dev-util"
    if existing_category:
        category.mkdir()
        (category / "unrelated").write_text("other writer")
    real_open, real_fsync = os.open, os.fsync

    def failing_open(path, flags, *args, **kwargs):
        if failure == "open" and path == "metadata.xml":
            raise OSError("simulated disk failure")
        return real_open(path, flags, *args, **kwargs)

    def failing_fsync(fd):
        if failure == "fsync":
            raise OSError("simulated disk failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "open", failing_open)
    monkeypatch.setattr(os, "fsync", failing_fsync)
    assert invoke(arguments(overlay, "--write")) == 1
    assert "simulated disk failure" in capsys.readouterr().err
    if existing_category:
        assert [p.name for p in category.iterdir()] == ["unrelated"]
        assert (category / "unrelated").read_text() == "other writer"
    else:
        assert not category.exists()


@pytest.mark.parametrize("close_at", ["stage", "category", "root", "scan"])
@pytest.mark.parametrize("write_failure", [False, True])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
def test_descriptor_close_errors_continue_cleanup_without_masking_failure_or_retrying(
    overlay, tmp_path, monkeypatch, capsys, close_at, write_failure, close_errno
):
    import errno
    import os

    category = overlay / "dev-util"
    target = category / "example-bin"
    real_open, real_close, real_fsync = os.open, os.close, os.fsync
    stage_fd = None
    scan_fd = None
    closed_category = False
    triggered = False
    armed = False
    diagnostic_path = None
    sentinel_fd = None

    def observe_open(path, flags, *args, **kwargs):
        nonlocal stage_fd, scan_fd
        fd = real_open(path, flags, *args, **kwargs)
        if str(path).startswith(".create-ebuild-"):
            stage_fd = fd
        if path == ".":
            scan_fd = fd
        return fd

    def fail_fsync(fd):
        nonlocal armed
        armed = True
        if write_failure:
            raise OSError(errno.EIO, "original fsync failure")
        return real_fsync(fd)

    def fail_close(fd):
        nonlocal triggered, diagnostic_path, sentinel_fd, closed_category
        path = os.readlink(f"/proc/self/fd/{fd}")
        selected = (
            (close_at == "stage" and fd == stage_fd)
            or (close_at == "scan" and fd == scan_fd)
            or (close_at == "category" and path == str(category))
            or (close_at == "root" and path == str(overlay) and closed_category)
        )
        if path == str(category):
            closed_category = True
        real_close(fd)
        if armed and selected and not triggered:
            triggered = True
            diagnostic_path = path
            # The kernel released the FD. Another open can reuse it before close raises.
            sentinel_fd = real_open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
            if sentinel_fd != fd:
                os.dup2(sentinel_fd, fd)
                real_close(sentinel_fd)
                sentinel_fd = fd
            raise OSError(getattr(errno, close_errno), "injected descriptor close failure")

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(os, "close", fail_close)
    try:
        assert invoke(arguments(overlay, "--write")) == 1
        captured = capsys.readouterr()
        assert triggered
        assert "injected descriptor close failure" in captured.err
        assert "descriptor state uncertain; not retried" in captured.err
        assert diagnostic_path in captured.err
        assert "cleanup incomplete" in captured.err
        assert "Written starter" not in captured.out
        assert sentinel_fd is not None
        os.fstat(sentinel_fd)
        if write_failure:
            assert "original fsync failure" in captured.err
            assert not category.exists()
        elif close_at == "scan":
            assert "cleanup failed before publication" in captured.err
            assert not category.exists()
        else:
            assert "published starter retained" in captured.err
            assert sorted(p.name for p in target.iterdir()) == [
                "example-bin-1.2.3.ebuild",
                "metadata.xml",
            ]
        leaked = []
        for entry in Path("/proc/self/fd").iterdir():
            try:
                if os.readlink(entry).startswith(str(overlay)):
                    leaked.append(int(entry.name))
            except FileNotFoundError:
                pass
        assert leaked == []
    finally:
        if sentinel_fd is not None:
            real_close(sentinel_fd)
        # Keep a failing regression run from leaking descriptors into other tests.
        for entry in Path("/proc/self/fd").iterdir():
            try:
                if os.readlink(entry).startswith(str(overlay)):
                    real_close(int(entry.name))
            except FileNotFoundError:
                pass


@pytest.mark.parametrize("walk", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
def test_each_root_traversal_parent_close_failure_releases_child_without_retry(
    overlay, tmp_path, monkeypatch, capsys, walk, close_errno
):
    import errno
    import os

    real_open, real_close = os.open, os.close

    # Exercise every parent, including / and the immediate overlay parent,
    # during validation, initial writing and all three attachment checks.
    def check_parent(parent):
        owned = {}
        walks = 0
        triggered = False
        sentinel_fd = None

        def observe_open(path, flags, *args, **kwargs):
            nonlocal walks
            fd = real_open(path, flags, *args, **kwargs)
            if path == "/":
                walks += 1
            if flags & os.O_DIRECTORY:
                owned[fd] = os.readlink(f"/proc/self/fd/{fd}")
            return fd

        def fail_close(fd):
            nonlocal triggered, sentinel_fd
            path = os.readlink(f"/proc/self/fd/{fd}")
            owned.pop(fd, None)
            real_close(fd)
            if walks == walk and path == str(parent) and not triggered:
                triggered = True
                sentinel_fd = real_open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
                if sentinel_fd != fd:
                    os.dup2(sentinel_fd, fd)
                    real_close(sentinel_fd)
                    sentinel_fd = fd
                raise OSError(getattr(errno, close_errno), "injected traversal close failure")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(os, "open", observe_open)
                patch.setattr(os, "close", fail_close)
                assert invoke(arguments(overlay, "--write")) == 1
            captured = capsys.readouterr()
            assert triggered
            assert "injected traversal close failure" in captured.err
            assert f"{parent}: close failed:" in captured.err
            assert "descriptor state uncertain; not retried" in captured.err
            assert "Written starter" not in captured.out
            assert sentinel_fd is not None
            os.fstat(sentinel_fd)
            assert owned == {}, f"leaked child descriptors after closing {parent}: {owned}"
            assert not (overlay / "dev-util").exists()
        finally:
            if sentinel_fd is not None:
                with suppress(OSError):
                    real_close(sentinel_fd)
            for fd in owned:
                with suppress(OSError):
                    real_close(fd)

    for parent in reversed(overlay.parents):
        check_parent(parent)


@pytest.mark.parametrize("failure_at", ["open", "parent-close"])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
def test_traversal_cleanup_close_errors_preserve_primary_and_each_owned_child(
    overlay, tmp_path, monkeypatch, capsys, failure_at, close_errno
):
    import errno
    import os

    real_open, real_close = os.open, os.close
    owned = {}
    sentinels = []
    failed = False
    close_paths = []

    def fail_open(path, flags, *args, **kwargs):
        nonlocal failed
        if failure_at == "open" and path == overlay.name and not failed:
            failed = True
            raise OSError(errno.EIO, "original traversal open failure")
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            owned[fd] = os.readlink(f"/proc/self/fd/{fd}")
        return fd

    def fail_close(fd):
        nonlocal failed
        path = os.readlink(f"/proc/self/fd/{fd}")
        owned.pop(fd, None)
        real_close(fd)
        if path == str(overlay.parent) or (failure_at == "parent-close" and path == str(overlay)):
            failed = True
            close_paths.append(path)
            sentinel = real_open(tmp_path / f"sentinel-{len(sentinels)}", os.O_CREAT | os.O_RDWR)
            if sentinel != fd:
                os.dup2(sentinel, fd)
                real_close(sentinel)
                sentinel = fd
            sentinels.append(sentinel)
            raise OSError(getattr(errno, close_errno), "secondary traversal cleanup close failure")

    monkeypatch.setattr(os, "open", fail_open)
    monkeypatch.setattr(os, "close", fail_close)
    try:
        assert invoke(arguments(overlay, "--write")) == 1
        captured = capsys.readouterr()
        assert failed
        assert "secondary traversal cleanup close failure" in captured.err
        assert "descriptor state uncertain; not retried" in captured.err
        assert f"{overlay.parent}: close failed:" in captured.err
        if failure_at == "open":
            assert "original traversal open failure" in captured.err
            assert close_paths == [str(overlay.parent)]
        else:
            assert f"{overlay}: close failed:" in captured.err
            assert close_paths == [str(overlay.parent), str(overlay)]
        for fd in sentinels:
            os.fstat(fd)
        assert owned == {}
        assert not captured.out
        assert not (overlay / "dev-util").exists()
    finally:
        for fd in [*sentinels, *owned]:
            with suppress(OSError):
                real_close(fd)


@pytest.mark.parametrize("walk", [3, 4, 5])
@pytest.mark.parametrize("fstat_failure", [False, True])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
def test_attachment_root_close_retains_primary_error_and_uncertain_absolute_path(
    overlay, tmp_path, monkeypatch, capsys, walk, fstat_failure, close_errno
):
    import errno
    import os

    real_open, real_close, real_fstat = os.open, os.close, os.fstat
    walks = 0
    owned = {}
    triggered = False
    sentinel_fd = None
    fstat_failed = False

    def observe_open(path, flags, *args, **kwargs):
        nonlocal walks
        fd = real_open(path, flags, *args, **kwargs)
        if path == "/":
            walks += 1
        if flags & os.O_DIRECTORY:
            owned[fd] = os.readlink(f"/proc/self/fd/{fd}")
        return fd

    def fail_fstat(fd):
        nonlocal fstat_failed
        if fstat_failure and walks == walk and owned.get(fd) == str(overlay) and not triggered:
            fstat_failed = True
            raise OSError(errno.EIO, "original attachment fstat failure")
        return real_fstat(fd)

    def fail_close(fd):
        nonlocal triggered, sentinel_fd
        path = os.readlink(f"/proc/self/fd/{fd}")
        owned.pop(fd, None)
        real_close(fd)
        if walks == walk and path == str(overlay) and not triggered:
            triggered = True
            sentinel_fd = real_open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
            if sentinel_fd != fd:
                os.dup2(sentinel_fd, fd)
                real_close(sentinel_fd)
                sentinel_fd = fd
            raise OSError(getattr(errno, close_errno), "secondary attachment close failure")

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "fstat", fail_fstat)
    monkeypatch.setattr(os, "close", fail_close)
    try:
        assert invoke(arguments(overlay, "--write")) == 1
        captured = capsys.readouterr()
        assert triggered
        assert "secondary attachment close failure" in captured.err
        assert f"{overlay}: close failed:" in captured.err
        assert "descriptor state uncertain; not retried" in captured.err
        assert "cleanup incomplete" in captured.err
        if fstat_failure:
            assert fstat_failed
            assert "original attachment fstat failure" in captured.err
        assert "Written starter" not in captured.out
        assert sentinel_fd is not None
        real_fstat(sentinel_fd)
        assert owned == {}
        assert not (overlay / "dev-util").exists()
    finally:
        if sentinel_fd is not None:
            with suppress(OSError):
                real_close(sentinel_fd)
        for fd in owned:
            with suppress(OSError):
                real_close(fd)


@pytest.mark.parametrize("close_at", ["root", "category", "profiles", "metadata"])
@pytest.mark.parametrize("primary_failure", [False, True])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
def test_validation_descriptor_close_errors_preserve_failure_and_other_owners(
    overlay, tmp_path, monkeypatch, capsys, close_at, primary_failure, close_errno, mode
):
    import errno
    import os

    category = overlay / "dev-util"
    category.mkdir()
    close_path = (
        overlay
        if close_at == "root"
        else overlay / ("dev-util" if close_at == "category" else close_at)
    )
    primary_path = close_path
    if close_at == "profiles":
        primary_path /= "repo_name"
    elif close_at == "metadata":
        primary_path /= "layout.conf"
    real_open, real_close, real_fstat, real_stat = os.open, os.close, os.fstat, os.stat
    owned = {}
    triggered = False
    primary_failed = False
    sentinel_fd = None

    def observe_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            owned[fd] = os.readlink(f"/proc/self/fd/{fd}")
        return fd

    def fail_fstat(fd):
        nonlocal primary_failed
        if (
            primary_failure
            and close_at != "category"
            and not primary_failed
            and os.readlink(f"/proc/self/fd/{fd}") == str(primary_path)
        ):
            primary_failed = True
            raise OSError(errno.EIO, "original validation failure")
        return real_fstat(fd)

    def fail_stat(path, *args, **kwargs):
        nonlocal primary_failed
        if primary_failure and close_at == "category" and path == "example-bin":
            primary_failed = True
            raise OSError(errno.EIO, "original validation failure")
        return real_stat(path, *args, **kwargs)

    def fail_close(fd):
        nonlocal triggered, sentinel_fd
        path = os.readlink(f"/proc/self/fd/{fd}")
        owned.pop(fd, None)
        real_close(fd)
        if path == str(close_path) and not triggered:
            triggered = True
            sentinel_fd = real_open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
            if sentinel_fd != fd:
                os.dup2(sentinel_fd, fd)
                real_close(sentinel_fd)
                sentinel_fd = fd
            raise OSError(getattr(errno, close_errno), "secondary validation close failure")

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "fstat", fail_fstat)
    monkeypatch.setattr(os, "stat", fail_stat)
    monkeypatch.setattr(os, "close", fail_close)
    try:
        assert invoke(arguments(overlay, mode)) == 1
        captured = capsys.readouterr()
        assert triggered
        assert f"{close_path}: close failed:" in captured.err
        assert "secondary validation close failure" in captured.err
        assert "descriptor state uncertain; not retried" in captured.err
        if primary_failure:
            assert primary_failed
            assert "original validation failure" in captured.err
        assert not captured.out
        assert sentinel_fd is not None
        real_fstat(sentinel_fd)
        assert owned == {}
        assert list(category.iterdir()) == []
    finally:
        if sentinel_fd is not None:
            with suppress(OSError):
                real_close(sentinel_fd)
        for fd in owned:
            with suppress(OSError):
                real_close(fd)


@pytest.mark.parametrize("marker_name", ["profiles/repo_name", "metadata/layout.conf"])
@pytest.mark.parametrize("failure_at", ["fdopen", "fstat", "read", "none"])
@pytest.mark.parametrize("close_errno", ["EIO", "EINTR"])
def test_marker_descriptor_transfer_and_close_preserve_failure_without_retry_or_leak(
    overlay, tmp_path, monkeypatch, capsys, marker_name, failure_at, close_errno
):
    import errno
    import os

    marker_path = overlay / marker_name
    real_fdopen, real_fstat, real_close, real_open = os.fdopen, os.fstat, os.close, os.open
    sentinel_fd = None
    close_attempts = 0

    def fail_after_release(fd):
        nonlocal sentinel_fd, close_attempts
        close_attempts += 1
        sentinel_fd = real_open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600)
        if sentinel_fd != fd:
            os.dup2(sentinel_fd, fd)
            real_close(sentinel_fd)
            sentinel_fd = fd
        raise OSError(getattr(errno, close_errno), "secondary marker close failure")

    class Marker:
        def __init__(self, source):
            self.source = source

        def fileno(self):
            return self.source.fileno()

        def read(self, count):
            if failure_at == "read":
                raise OSError(errno.EIO, "original marker read failure")
            return self.source.read(count)

        def close(self):
            fd = self.source.fileno()
            self.source.close()
            fail_after_release(fd)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fail_fdopen(fd, *args, **kwargs):
        if os.readlink(f"/proc/self/fd/{fd}") == str(marker_path):
            if failure_at == "fdopen":
                raise OSError(errno.EIO, "original marker fdopen failure")
            return Marker(real_fdopen(fd, *args, **kwargs))
        return real_fdopen(fd, *args, **kwargs)

    def fail_fstat(fd):
        if failure_at == "fstat" and os.readlink(f"/proc/self/fd/{fd}") == str(marker_path):
            raise OSError(errno.EIO, "original marker fstat failure")
        return real_fstat(fd)

    def fail_close(fd):
        path = os.readlink(f"/proc/self/fd/{fd}")
        real_close(fd)
        if failure_at == "fdopen" and path == str(marker_path):
            fail_after_release(fd)

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "fstat", fail_fstat)
    monkeypatch.setattr(os, "close", fail_close)
    try:
        assert invoke(arguments(overlay, "--write")) == 1
        captured = capsys.readouterr()
        if failure_at != "none":
            assert f"original marker {failure_at} failure" in captured.err
        assert "secondary marker close failure" in captured.err
        assert f"{marker_path}: close failed:" in captured.err
        assert "descriptor state uncertain; not retried" in captured.err
        assert close_attempts == 1
        assert sentinel_fd is not None
        real_fstat(sentinel_fd)
        assert not captured.out
        assert not (overlay / "dev-util").exists()
        for entry in Path("/proc/self/fd").iterdir():
            with suppress(FileNotFoundError):
                assert not os.readlink(entry).startswith(str(overlay))
    finally:
        if sentinel_fd is not None:
            with suppress(OSError):
                real_close(sentinel_fd)
        for entry in Path("/proc/self/fd").iterdir():
            with suppress(FileNotFoundError):
                if os.readlink(entry).startswith(str(overlay)):
                    real_close(int(entry.name))


@pytest.mark.parametrize("failure_at", ["fstat", "fdopen"])
@pytest.mark.parametrize("close_failure", [False, True])
def test_artifact_descriptor_is_closed_when_ownership_transfer_fails(
    overlay, monkeypatch, capsys, failure_at, close_failure
):
    import errno
    import os

    real_fstat, real_fdopen, real_close = os.fstat, os.fdopen, os.close
    artifact_fd = None
    artifact_path = None
    close_attempts = []

    def fail_transfer(fd):
        nonlocal artifact_fd, artifact_path
        path = os.readlink(f"/proc/self/fd/{fd}")
        if artifact_fd is None and path.endswith(".ebuild"):
            artifact_fd = fd
            artifact_path = path
            raise OSError(errno.EIO, f"injected artifact {failure_at} failure")

    def fail_fstat(fd):
        if failure_at == "fstat":
            fail_transfer(fd)
        return real_fstat(fd)

    def fail_fdopen(fd, *args, **kwargs):
        if failure_at == "fdopen":
            fail_transfer(fd)
        return real_fdopen(fd, *args, **kwargs)

    def observe_close(fd):
        path = os.readlink(f"/proc/self/fd/{fd}")
        if fd == artifact_fd:
            close_attempts.append(path)
        real_close(fd)
        if close_failure and path == artifact_path:
            raise OSError(errno.EIO, "injected artifact close failure")

    monkeypatch.setattr(os, "fstat", fail_fstat)
    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "close", observe_close)
    try:
        assert invoke(arguments(overlay, "--write")) == 1
        captured = capsys.readouterr()
        assert artifact_fd is not None
        assert artifact_path is not None
        assert f"injected artifact {failure_at} failure" in captured.err
        assert "Written starter" not in captured.out
        assert close_attempts.count(artifact_path) == 1
        if close_failure:
            assert "injected artifact close failure" in captured.err
            assert "descriptor state uncertain; not retried" in captured.err
            assert artifact_path in captured.err
        for entry in Path("/proc/self/fd").iterdir():
            with suppress(FileNotFoundError):
                assert not os.readlink(entry).startswith(str(overlay))
        if failure_at == "fstat":
            assert "cleanup incomplete" in captured.err
            assert artifact_path in captured.err
            assert Path(artifact_path).read_bytes() == b""
        else:
            assert not (overlay / "dev-util").exists()
    finally:
        for entry in Path("/proc/self/fd").iterdir():
            try:
                if os.readlink(entry).startswith(str(overlay)):
                    real_close(int(entry.name))
            except FileNotFoundError:
                pass


@pytest.mark.parametrize("write_failure", [False, True])
def test_artifact_file_close_error_is_reported_without_masking_write_failure(
    overlay, monkeypatch, capsys, write_failure
):
    import errno
    import os

    real_fdopen, real_fsync = os.fdopen, os.fsync
    artifact_path = None

    class FailingClose:
        def __init__(self, output):
            self.output = output

        def write(self, content):
            return self.output.write(content)

        def flush(self):
            return self.output.flush()

        def fileno(self):
            return self.output.fileno()

        def close(self):
            self.output.close()
            raise OSError(errno.EIO, "injected file-object close failure")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def failing_fdopen(fd, *args, **kwargs):
        nonlocal artifact_path
        path = os.readlink(f"/proc/self/fd/{fd}")
        output = real_fdopen(fd, *args, **kwargs)
        if path.endswith(".ebuild"):
            artifact_path = path
            return FailingClose(output)
        return output

    def failing_fsync(fd):
        if write_failure:
            raise OSError(errno.EIO, "original artifact write failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    monkeypatch.setattr(os, "fsync", failing_fsync)
    assert invoke(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert artifact_path is not None
    assert "injected file-object close failure" in captured.err
    assert "descriptor state uncertain; not retried" in captured.err
    assert artifact_path in captured.err
    assert "cleanup incomplete" in captured.err
    assert "Written starter" not in captured.out
    if write_failure:
        assert "original artifact write failure" in captured.err
    else:
        assert "cleanup failed before publication" in captured.err
    assert not (overlay / "dev-util").exists()
    for entry in Path("/proc/self/fd").iterdir():
        with suppress(FileNotFoundError):
            assert not os.readlink(entry).startswith(str(overlay))


@pytest.mark.parametrize("existing_category", [False, True])
@pytest.mark.parametrize("failure_at", ["root-open", "package-stat"])
def test_final_attachment_io_failure_rolls_back_and_allows_retry(
    overlay, monkeypatch, capsys, existing_category, failure_at
):
    import os

    category = overlay / "dev-util"
    target = category / "example-bin"
    if existing_category:
        category.mkdir()
        (category / "unrelated").write_text("other writer")
    real_open, real_stat = os.open, os.stat
    failed = False

    def fail_after_publication(path, flags, *args, **kwargs):
        nonlocal failed
        if failure_at == "root-open" and path == "/" and target.exists() and not failed:
            failed = True
            raise OSError("simulated final attachment failure")
        return real_open(path, flags, *args, **kwargs)

    def fail_package_stat(path, *args, **kwargs):
        nonlocal failed
        if failure_at == "package-stat" and path == target.name and target.exists() and not failed:
            failed = True
            raise OSError("simulated final attachment failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_after_publication)
    monkeypatch.setattr(os, "stat", fail_package_stat)
    assert invoke(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert failed
    assert "simulated final attachment failure" in captured.err
    assert "cleanup incomplete" not in captured.err
    assert "Written starter" not in captured.out
    assert not target.exists()
    if existing_category:
        assert [p.name for p in category.iterdir()] == ["unrelated"]
        assert (category / "unrelated").read_text() == "other writer"
    else:
        assert not category.exists()
    assert invoke(arguments(overlay, "--write")) == 0
    assert "Written starter only" in capsys.readouterr().out
    assert sorted(p.name for p in target.iterdir()) == [
        "example-bin-1.2.3.ebuild",
        "metadata.xml",
    ]


@pytest.mark.parametrize(
    "mutation",
    ["unknown-file", "replaced-file", "replaced-target", "unlink-failure", "stat-failure"],
)
def test_failed_postpublication_verification_preserves_and_reports_residual_outputs(
    overlay, tmp_path, monkeypatch, capsys, mutation
):
    import os

    target = overlay / "dev-util/example-bin"
    artifact = target / "example-bin-1.2.3.ebuild"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("other writer")
    moved = tmp_path / "moved-package"
    real_open, real_unlink, real_stat = os.open, os.unlink, os.stat
    failed = False

    def fail_after_publication(path, flags, *args, **kwargs):
        nonlocal failed
        if path == "/" and target.exists() and not failed:
            failed = True
            if mutation == "unknown-file":
                (target / "unknown").write_text("other writer")
            elif mutation == "replaced-file":
                artifact.rename(tmp_path / "original-artifact")
                artifact.write_text("other writer")
            elif mutation == "replaced-target":
                target.rename(moved)
                target.symlink_to(outside, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)
            raise OSError("simulated final attachment failure")
        return real_open(path, flags, *args, **kwargs)

    def fail_unlink(path, *args, **kwargs):
        if failed and mutation == "unlink-failure" and path == artifact.name:
            raise OSError("simulated cleanup unlink failure")
        return real_unlink(path, *args, **kwargs)

    def fail_stat(path, *args, **kwargs):
        if failed and mutation == "stat-failure" and path == artifact.name:
            raise OSError("simulated cleanup stat failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_after_publication)
    monkeypatch.setattr(os, "unlink", fail_unlink)
    monkeypatch.setattr(os, "stat", fail_stat)
    assert invoke(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert failed
    expected_failure = (
        "published package changed"
        if mutation == "replaced-target"
        else "simulated final attachment failure"
    )
    assert expected_failure in captured.err
    assert "cleanup incomplete" in captured.err
    assert "Written starter" not in captured.out
    if mutation == "replaced-target":
        assert str(target) in captured.err
        assert target.is_symlink()
        assert list(moved.iterdir()) == []
        assert [p.name for p in outside.iterdir()] == ["keep"]
        assert (outside / "keep").read_text() == "other writer"
    else:
        residual = target / "unknown" if mutation == "unknown-file" else artifact
        assert str(residual) in captured.err
        assert [p.name for p in target.iterdir()] == [residual.name]
        if mutation in {"unknown-file", "replaced-file"}:
            assert residual.read_text() == "other writer"
        else:
            assert "EAPI=8" in residual.read_text()
    assert [p.name for p in target.parent.iterdir()] == ["example-bin"]


@pytest.mark.parametrize("racer", ["empty-directory", "artifact", "symlink"])
def test_atomic_publication_never_replaces_a_racing_target(
    overlay, tmp_path, monkeypatch, capsys, racer
):
    import os

    outside = tmp_path / "outside"
    outside.mkdir()
    target = overlay / "dev-util/example-bin"
    real_fsync = os.fsync
    raced = False

    def race(fd):
        nonlocal raced
        if not raced:
            raced = True
            if racer == "symlink":
                target.symlink_to(outside, target_is_directory=True)
            else:
                target.mkdir()
                if racer == "artifact":
                    (target / "metadata.xml").write_text("other writer")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", race)
    assert invoke(arguments(overlay, "--write")) == 1
    assert capsys.readouterr().err
    assert [p.name for p in target.parent.iterdir()] == ["example-bin"]
    if racer == "artifact":
        assert (target / "metadata.xml").read_text() == "other writer"
    else:
        assert list(target.iterdir()) == []
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("existing_category", [False, True])
def test_category_swap_during_write_does_not_follow_or_recursively_delete_symlink(
    overlay, tmp_path, monkeypatch, capsys, existing_category
):
    import os

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched")
    category = overlay / "dev-util"
    if existing_category:
        category.mkdir()
    moved = overlay / "moved-category"
    real_fsync = os.fsync
    raced = False

    def swap(fd):
        nonlocal raced
        if not raced:
            raced = True
            category.rename(moved)
            category.symlink_to(outside, target_is_directory=True)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", swap)
    assert invoke(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert "refusing publication" in captured.err
    assert "cleanup incomplete" in captured.err
    assert f"{category} moved or replaced; left untouched" in captured.err
    assert "Written starter" not in captured.out
    assert category.is_symlink()
    assert list(moved.iterdir()) == []
    assert [p.name for p in outside.iterdir()] == ["keep"]
    assert (outside / "keep").read_text() == "untouched"


def test_staging_directory_swapped_for_symlink_is_not_traversed_on_cleanup(
    overlay, tmp_path, monkeypatch, capsys
):
    import os

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched")
    real_fsync = os.fsync
    swapped = False
    moved = overlay / "dev-util/moved-stage"

    def swap(fd):
        nonlocal swapped
        if not swapped:
            swapped = True
            stage = next((overlay / "dev-util").glob(".create-ebuild-*"))
            stage.rename(moved)
            stage.symlink_to(outside, target_is_directory=True)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", swap)
    assert invoke(arguments(overlay, "--write")) == 1
    assert "cleanup incomplete" in capsys.readouterr().err
    assert not (overlay / "dev-util/example-bin").exists()
    assert list(moved.iterdir()) == []
    stage = next((overlay / "dev-util").glob(".create-ebuild-*"))
    assert stage.is_symlink()
    assert [p.name for p in outside.iterdir()] == ["keep"]
    assert (outside / "keep").read_text() == "untouched"


def test_cleanup_preserves_another_writers_replaced_file(overlay, monkeypatch, capsys):
    import os

    real_fsync = os.fsync
    replaced = False

    def replace(fd):
        nonlocal replaced
        if not replaced:
            replaced = True
            stage = next((overlay / "dev-util").glob(".create-ebuild-*"))
            path = stage / "example-bin-1.2.3.ebuild"
            path.unlink()
            path.write_text("another writer's data")
            raise OSError("simulated replacement then I/O failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", replace)
    assert invoke(arguments(overlay, "--write")) == 1
    assert "cleanup incomplete" in capsys.readouterr().err
    assert not (overlay / "dev-util/example-bin").exists()
    stage = next((overlay / "dev-util").glob(".create-ebuild-*"))
    assert [p.name for p in stage.iterdir()] == ["example-bin-1.2.3.ebuild"]
    assert (stage / "example-bin-1.2.3.ebuild").read_text() == "another writer's data"


@pytest.mark.parametrize("directory_kind", ["category", "stage"])
@pytest.mark.parametrize("replaced", [False, True])
def test_stat_failure_after_mkdir_preserves_unidentified_entry_and_reports_exact_path(
    overlay, tmp_path, monkeypatch, capsys, directory_kind, replaced
):
    import os

    real_mkdir, real_stat, real_rmdir = os.mkdir, os.stat, os.rmdir
    created_path: Path | None = None
    identity_reads = 0
    removed_names: list[str] = []

    def observe_mkdir(path, *args, **kwargs):
        nonlocal created_path
        result = real_mkdir(path, *args, **kwargs)
        if (directory_kind == "category" and path == "dev-util") or (
            directory_kind == "stage" and str(path).startswith(".create-ebuild-")
        ):
            path_to_preserve = overlay / "dev-util"
            if directory_kind == "stage":
                path_to_preserve /= path
            created_path = path_to_preserve
            if replaced:
                path_to_preserve.rename(tmp_path / "unidentified-original")
                real_mkdir(path_to_preserve)
                (path_to_preserve / "keep").write_text("other writer")
        return result

    def fail_identity_read(path, *args, **kwargs):
        nonlocal identity_reads
        if created_path is not None and path == created_path.name:
            identity_reads += 1
            raise OSError("simulated post-mkdir stat failure")
        return real_stat(path, *args, **kwargs)

    def observe_rmdir(path, *args, **kwargs):
        removed_names.append(path)
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", observe_mkdir)
    monkeypatch.setattr(os, "stat", fail_identity_read)
    monkeypatch.setattr(os, "rmdir", observe_rmdir)
    assert invoke(arguments(overlay, "--write")) == 1
    captured = capsys.readouterr()
    assert "simulated post-mkdir stat failure" in captured.err
    assert "cleanup incomplete" in captured.err
    assert "Written starter" not in captured.out
    assert created_path is not None
    assert str(created_path) in captured.err
    assert identity_reads == 1
    assert created_path.name not in removed_names
    assert not (overlay / "dev-util/example-bin").exists()
    assert created_path.is_dir()
    if replaced:
        assert [p.name for p in created_path.iterdir()] == ["keep"]
        assert (created_path / "keep").read_text() == "other writer"
        assert list((tmp_path / "unidentified-original").iterdir()) == []
    else:
        assert list(created_path.iterdir()) == []


def test_io_failure_opening_new_category_cleans_the_empty_owned_directory(overlay, monkeypatch):
    import os

    real_open = os.open

    def fail(path, flags, *args, **kwargs):
        if path == "dev-util" and (overlay / "dev-util").exists():
            raise PermissionError("simulated category open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail)
    assert invoke(arguments(overlay, "--write")) == 1
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize("field", ["--upstream-url", "--homepage"])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/@@BINARY_NAME@@",
        "https://example.org/@@HOMEPAGE@@/@@UNKNOWN@@/@@",
    ],
)
def test_url_template_markers_remain_literal_in_preview_and_write(
    overlay, capsys, mode, field, url
):
    args = arguments(overlay, mode, field, url)
    assert invoke(args) == 0
    output = capsys.readouterr().out
    expected = (
        f'SRC_URI="{url} -> ${{P}}.bin"' if field == "--upstream-url" else f"HOMEPAGE='{url}'"
    )
    assert expected in output
    if mode == "--write":
        ebuild = overlay / "dev-util/example-bin/example-bin-1.2.3.ebuild"
        assert expected in ebuild.read_text()
        assert sorted(p.name for p in ebuild.parent.iterdir()) == [ebuild.name, "metadata.xml"]
    else:
        assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize("filename", ["binary-direct.ebuild.in", "metadata.xml.in"])
@pytest.mark.parametrize(
    "marker", ["@@UNKNOWN@@", "@@BINARY_NAME", "@@", "@@binary_name@@", "@@BAD-NAME@@"]
)
def test_invalid_template_placeholders_fail_before_category_creation(
    overlay, monkeypatch, capsys, mode, filename, marker
):
    real_read = Path.read_text

    def unresolved(path, *args, **kwargs):
        content = real_read(path, *args, **kwargs)
        return content + marker if path.name == filename else content

    monkeypatch.setattr(Path, "read_text", unresolved)
    assert invoke(arguments(overlay, mode)) == 1
    captured = capsys.readouterr()
    assert f"unresolved template placeholder in {filename}" in captured.err
    assert not captured.out
    assert sorted(path.name for path in overlay.iterdir()) == ["metadata", "profiles"]


def test_dangling_category_and_metadata_symlinks_fail_closed(overlay, tmp_path):
    (overlay / "dev-util").symlink_to(tmp_path / "missing", target_is_directory=True)
    assert invoke(arguments(overlay, "--write")) == 1
    assert not (tmp_path / "missing").exists()
    (overlay / "dev-util").unlink()
    marker = overlay / "profiles/repo_name"
    original = tmp_path / "original-repo-name"
    marker.rename(original)
    marker.symlink_to(original)
    assert invoke(arguments(overlay, "--write")) == 1
    assert not (overlay / "dev-util").exists()
    assert original.read_text() == "test-overlay\n"


def test_write_requires_the_exact_long_option(overlay):
    with pytest.raises(SystemExit) as exc:
        invoke(arguments(overlay, "--w"))
    assert exc.value.code == 2
    assert not (overlay / "dev-util").exists()


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize(
    "email", ["owner..name@example.org", "owner.@example.org", ".owner@example.org"]
)
def test_launcher_rejects_malformed_maintainer_local_dots_before_any_artifact(
    overlay, tmp_path, mode, email
):
    import subprocess

    launcher = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    before = {p.relative_to(overlay): p.read_bytes() for p in overlay.rglob("*") if p.is_file()}
    result = subprocess.run(
        [str(launcher), *arguments(overlay, mode, "--maintainer-email", email)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout
    assert "maintainer-email must be an explicit plain email address" in result.stderr
    assert not result.stdout
    assert sorted(p.name for p in overlay.iterdir()) == ["metadata", "profiles"]
    assert {
        p.relative_to(overlay): p.read_bytes() for p in overlay.rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
@pytest.mark.parametrize(
    "email",
    [
        "owner.name@example.org",
        "owner_name@example.org",
        "o'owner@example.org",
        "owner-name@example.org",
        "owner+name@example.org",
    ],
)
def test_launcher_preserves_valid_maintainer_local_parts_and_xml_text(
    overlay, tmp_path, mode, email
):
    import subprocess
    import xml.etree.ElementTree as ET

    launcher = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    result = subprocess.run(
        [
            str(launcher),
            *arguments(
                overlay, mode, "--maintainer-email", email, "--maintainer-name", "O'Brian & <Team>"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert f"<email>{email}</email>" in result.stdout
    assert "<name>O'Brian &amp; &lt;Team&gt;</name>" in result.stdout
    if mode == "--write":
        assert "Written starter only" in result.stdout
        package = overlay / "dev-util/example-bin"
        assert sorted(p.name for p in package.iterdir()) == [
            "example-bin-1.2.3.ebuild",
            "metadata.xml",
        ]
        text = (package / "metadata.xml").read_text(encoding="utf-8")
    else:
        assert "Preview only" in result.stdout
        assert sorted(p.name for p in overlay.iterdir()) == ["metadata", "profiles"]
        text = result.stdout.split("--- dev-util/example-bin/metadata.xml ---\n", 1)[1].split(
            "\nNot an installable package yet.", 1
        )[0]
    assert f"<email>{email}</email>" in text
    assert "<name>O'Brian &amp; &lt;Team&gt;</name>" in text
    metadata = ET.fromstring(text)
    assert metadata.findtext("maintainer/email") == email
    assert metadata.findtext("maintainer/name") == "O'Brian & <Team>"


def test_launcher_runs_offline_preview_write_and_refusal_from_another_directory(overlay, tmp_path):
    import subprocess
    import xml.etree.ElementTree as ET

    launcher = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    preview = subprocess.run(
        [str(launcher), *arguments(overlay)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert preview.returncode == 0, preview.stderr
    assert "Preview only" in preview.stdout
    assert not (overlay / "dev-util").exists()
    applied = subprocess.run(
        [str(launcher), *arguments(overlay, "--write")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr
    package = overlay / "dev-util/example-bin"
    assert ET.parse(package / "metadata.xml").findtext("maintainer/email") == "owner@example.org"
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    refused = subprocess.run(
        [str(launcher), *arguments(overlay, "--write")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 1
    assert "already exists" in refused.stderr
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before


@pytest.mark.parametrize("import_source", ["cwd", "pythonpath"])
@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
def test_launcher_never_imports_checkout_or_pythonpath_code(overlay, tmp_path, import_source, mode):
    import os
    import subprocess

    shadow = overlay if import_source == "cwd" else tmp_path / "external-imports"
    shadow.mkdir(exist_ok=True)
    marker = tmp_path / "executed-untrusted-code"
    payload = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    (shadow / "sitecustomize.py").write_text(payload)
    package = shadow / "overlay_tools"
    (package / "cli").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli/__init__.py").write_text("")
    (package / "cli/create_ebuild.py").write_text(payload)
    env = dict(os.environ)
    if import_source == "pythonpath":
        env["PYTHONPATH"] = str(shadow)
    else:
        env.pop("PYTHONPATH", None)
    launcher = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    result = subprocess.run(
        [str(launcher), *arguments(overlay, mode)],
        cwd=overlay,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not marker.exists(), result.stderr
    assert result.returncode == 0, result.stderr
    expected_report = "Written starter only" if mode == "--write" else "Preview only"
    assert expected_report in result.stdout
    target = overlay / "dev-util/example-bin"
    if mode == "--write":
        assert sorted(p.name for p in target.iterdir()) == [
            "example-bin-1.2.3.ebuild",
            "metadata.xml",
        ]
    else:
        assert not (overlay / "dev-util").exists()
    assert not list(shadow.rglob("__pycache__"))


@pytest.mark.parametrize("mode", ["--dry-run", "--write"])
def test_launcher_uses_its_own_source_with_an_environment_prepared_for_another_checkout(
    overlay, tmp_path, mode
):
    import shutil
    import subprocess
    import venv

    import packaging

    original = Path(__file__).resolve().parents[1]
    own = tmp_path / "own-tools"
    foreign = tmp_path / "other-tools"
    for checkout in (own, foreign):
        shutil.copytree(original / "src", checkout / "src")
    shutil.copytree(original / "assets", own / "assets")
    launcher = own / "bin/create-ebuild"
    launcher.parent.mkdir()
    shutil.copy2(original / "bin/create-ebuild", launcher)
    marker = tmp_path / "imported-other-checkout"
    (foreign / "src/overlay_tools/cli/create_ebuild.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('wrong source')\n"
    )
    # Prepare a real isolated interpreter offline, with an editable source-path entry.
    venv.create(own / ".venv", with_pip=False)
    site_packages = list((own / ".venv/lib").glob("python*/site-packages"))
    assert len(site_packages) == 1
    shutil.copytree(Path(packaging.__file__).parent, site_packages[0] / "packaging")
    (site_packages[0] / "other-checkout.pth").write_text(str(foreign / "src") + "\n")
    prepared = subprocess.run(
        [
            str(own / ".venv/bin/python"),
            "-I",
            "-B",
            "-c",
            "import overlay_tools; print(overlay_tools.__file__)",
        ],
        cwd=overlay,
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    assert str(foreign / "src/overlay_tools/__init__.py") in prepared.stdout
    result = subprocess.run(
        [str(launcher), *arguments(overlay, mode)],
        cwd=overlay,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not marker.exists(), result.stderr
    assert result.returncode == 0, result.stderr
    assert ("Written starter only" if mode == "--write" else "Preview only") in result.stdout
    if mode == "--write":
        text = (overlay / "dev-util/example-bin/example-bin-1.2.3.ebuild").read_text()
        assert "DESCRIPTION='Example binary tool'" in text
    else:
        assert not (overlay / "dev-util").exists()


def test_unprepared_launcher_refuses_without_attempting_setup(overlay, tmp_path):
    import subprocess

    original = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    launcher = tmp_path / "unprepared-tools/bin/create-ebuild"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(original.read_bytes())
    launcher.chmod(0o755)
    result = subprocess.run(
        [str(launcher), *arguments(overlay, "--write")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 127
    assert "virtualenv is not prepared" in result.stderr
    assert not (launcher.parent.parent / ".venv").exists()
    assert not (overlay / "dev-util").exists()


def test_two_real_cli_writers_publish_exactly_one_complete_package(overlay, tmp_path):
    import subprocess

    launcher = Path(__file__).resolve().parents[1] / "bin/create-ebuild"
    processes = [
        subprocess.Popen(
            [str(launcher), *arguments(overlay, "--write")],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [p.communicate(timeout=20) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 1], outputs
    category = overlay / "dev-util"
    assert [p.name for p in category.iterdir()] == ["example-bin"]
    package = category / "example-bin"
    assert sorted(p.name for p in package.iterdir()) == ["example-bin-1.2.3.ebuild", "metadata.xml"]
