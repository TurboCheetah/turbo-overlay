"""Repository policy exercised through the public maintenance CLIs."""

import json
from pathlib import Path

import pytest

from overlay_tools.cli import check_updates, update_ebuild
from overlay_tools.core.github import ReleaseInfo
from overlay_tools.core.update_policy import load_update_exclusions

EXCLUSIONS = {
    "dev-util/t3code-bin": "Desktop deprecated; use upstream installers.",
    "dev-util/t3code-nightly-bin": "Desktop deprecated; use upstream installers.",
    "dev-util/t3code-openrc": "Locally versioned adapter; no upstream nightly bumps.",
}


def make_overlay(root: Path) -> Path:
    (root / "profiles").mkdir()
    (root / "profiles/repo_name").write_text("test-overlay\n")
    for atom in [*EXCLUSIONS, "net-im/goofcord"]:
        package = root / atom
        package.mkdir(parents=True)
        name = package.name
        repo = "Discord-Client/GoofCord" if name == "goofcord" else "pingdotgg/t3code"
        (package / f"{name}-1.ebuild").write_text(
            f'EAPI=8\nSRC_URI="https://github.com/{repo}/releases/download/v${{PV}}/app"\n'
        )
        (package / "Manifest").write_text("retained manifest\n")
    return root


def write_policy(root: Path, content: str | None = None) -> None:
    (root / "metadata").mkdir(exist_ok=True)
    (root / "metadata/update-exclusions.json").write_text(
        json.dumps(EXCLUSIONS) if content is None else content
    )


@pytest.fixture
def github_calls(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def get_latest_release(self, repo, channel=None):
            calls.append((repo, channel))
            return ReleaseInfo(tag="v2", version="2", url="https://example.invalid/v2")

    monkeypatch.setattr(check_updates, "GitHubClient", Client)
    return calls


@pytest.mark.parametrize(
    "atom", ["dev-util/t3code-bin", "dev-util/t3code-nightly-bin", "dev-util/t3code-openrc"]
)
def test_shipped_policy_excludes_t3_packages_at_check_cli(capsys, github_calls, atom):
    root = Path(__file__).resolve().parents[4]
    policy = json.loads((root / "metadata/update-exclusions.json").read_text())
    assert atom in policy

    result = check_updates.main(["--overlay-path", str(root), "--json", "--package", atom])
    captured = capsys.readouterr()

    assert result == 2
    assert json.loads(captured.out) == []
    assert f"Skipping {atom}: {policy[atom]}" in captured.err
    assert github_calls == []


def test_full_scan_skips_exclusions_before_upstream_lookup(tmp_path, capsys, github_calls):
    root = make_overlay(tmp_path)
    write_policy(root)

    result = check_updates.main(["--overlay-path", str(root), "--json"])
    captured = capsys.readouterr()

    assert github_calls == [("Discord-Client/GoofCord", None)]
    assert result == 0
    assert [package["name"] for package in json.loads(captured.out)] == ["goofcord"]
    for atom, reason in EXCLUSIONS.items():
        assert f"Skipping {atom}: {reason}" in captured.err


@pytest.mark.parametrize("atom", EXCLUSIONS)
def test_update_rejects_excluded_package_without_mutation(tmp_path, capsys, atom):
    root = make_overlay(tmp_path)
    write_policy(root)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }

    result = update_ebuild.main(
        ["--skip-git", "--skip-manifest", "--version", "2", str(root / atom)]
    )
    captured = capsys.readouterr()

    after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before
    assert result == 1
    message = " ".join((captured.out + captured.err).split())
    assert f"Update excluded for {atom}: {EXCLUSIONS[atom]}" in message


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        "null",
        '{"dev-util/t3code-bin": 1}',
        '{"dev-util/t3code-bin": null}',
        '{"dev-util/t3code-bin": ""}',
        '{"dev-util/t3code-bin": "   "}',
        '{"t3code-bin": "reason"}',
        '{"dev-util/*": "reason"}',
        '{"=dev-util/t3code-bin-1": "reason"}',
        '{"dev-util/t3code-bin-1": "reason"}',
        '{"dev-util/t3code-bin:0": "reason"}',
        '{"dev-util/t3code-bin::turbo-overlay": "reason"}',
        '{"../t3code-bin": "reason"}',
        '{" dev-util/t3code-bin": "reason"}',
        '{"dev-util/t3code-bin": "one", "dev-util/t3code-bin": "two"}',
    ],
)
def test_invalid_policy_fails_closed_at_both_clis(tmp_path, capsys, github_calls, content):
    root = make_overlay(tmp_path)
    write_policy(root, content)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }

    check_result = check_updates.main(["--overlay-path", str(root), "--json"])
    check_output = capsys.readouterr()
    update_result = update_ebuild.main(
        ["--skip-git", "--skip-manifest", "--version", "2", str(root / "net-im/goofcord")]
    )
    update_output = capsys.readouterr()

    assert github_calls == []
    assert check_result == update_result == 1
    assert "Invalid update policy" in check_output.err
    assert "Invalid update policy" in update_output.out + update_output.err
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("atom", EXCLUSIONS)
@pytest.mark.parametrize(
    "channel_args", [[], ["--channel", "nightly"], ["--exclude-channel", "nightly"]]
)
def test_explicit_excluded_target_reports_reason_and_no_updates(
    tmp_path, capsys, github_calls, atom, channel_args
):
    root = make_overlay(tmp_path)
    write_policy(root)

    result = check_updates.main(
        ["--overlay-path", str(root), "--json", "--package", atom, *channel_args]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert json.loads(captured.out) == []
    assert f"Skipping {atom}: {EXCLUSIONS[atom]}" in captured.err
    assert github_calls == []


@pytest.mark.parametrize("flags", [["--dry-run"], ["--pr"], ["--yes"], ["--skip-git"]])
def test_update_exclusion_precedes_git_manifest_or_fetch(tmp_path, monkeypatch, capsys, flags):
    root = make_overlay(tmp_path)
    write_policy(root)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Excluded packages must not reach external operations")

    for name in [
        "is_git_repo",
        "git_root",
        "git_fetch_branch",
        "git_checkout_branch",
        "run_ebuild_manifest",
        "run_egencache_update",
        "git_add",
        "git_commit",
        "git_push",
    ]:
        monkeypatch.setattr(update_ebuild, name, forbidden)

    result = update_ebuild.main([*flags, "--version", "2", str(root / "dev-util/t3code-bin")])

    assert result == 1
    assert calls == []
    assert "Update excluded" in capsys.readouterr().err


@pytest.mark.parametrize("policy", [False, True])
def test_unrelated_update_succeeds_with_missing_or_present_policy(tmp_path, policy):
    root = make_overlay(tmp_path)
    if policy:
        write_policy(root)

    result = update_ebuild.main(
        ["--skip-git", "--skip-manifest", "--version", "2", str(root / "net-im/goofcord")]
    )

    assert result == 0
    assert (root / "net-im/goofcord/goofcord-2.ebuild").is_file()
    assert (root / "net-im/goofcord/Manifest").read_text() == "retained manifest\n"


def test_missing_policy_keeps_existing_discovery_and_ignores_masks(tmp_path, capsys, github_calls):
    root = make_overlay(tmp_path)
    (root / "profiles/package.mask").write_text("dev-util/t3code-bin\n")

    result = check_updates.main(["--overlay-path", str(root), "--json"])
    captured = capsys.readouterr()

    assert result == 0
    assert {f"{p['category']}/{p['name']}" for p in json.loads(captured.out)} == {
        *EXCLUSIONS,
        "net-im/goofcord",
    }
    assert len(github_calls) == 4
    assert "Skipping" not in captured.err


def test_generic_policy_excludes_unrelated_atom_too(tmp_path, capsys, github_calls):
    root = make_overlay(tmp_path)
    write_policy(
        root, json.dumps({**EXCLUSIONS, "net-im/goofcord": "Temporary maintenance pause."})
    )

    result = check_updates.main(["--overlay-path", str(root), "--json"])
    captured = capsys.readouterr()

    assert result == 2
    assert json.loads(captured.out) == []
    assert github_calls == []
    assert "Skipping net-im/goofcord: Temporary maintenance pause." in captured.err


@pytest.mark.parametrize("invalid_file", ["directory", "invalid-utf8"])
def test_unreadable_policy_is_not_treated_as_missing(tmp_path, capsys, github_calls, invalid_file):
    root = make_overlay(tmp_path)
    (root / "metadata").mkdir()
    policy = root / "metadata/update-exclusions.json"
    if invalid_file == "directory":
        policy.mkdir()
    else:
        policy.write_bytes(b"\xff")

    result = check_updates.main(["--overlay-path", str(root), "--json"])
    captured = capsys.readouterr()
    update_result = update_ebuild.main(
        ["--skip-git", "--skip-manifest", "--version", "2", str(root / "net-im/goofcord")]
    )
    update_captured = capsys.readouterr()

    assert result == update_result == 1
    assert "Invalid update policy" in captured.err
    assert "Invalid update policy" in update_captured.err
    assert github_calls == []
    assert not (root / "net-im/goofcord/goofcord-2.ebuild").exists()


def test_checked_in_policy_excludes_the_retired_atoms():
    """The shipped policy, not only the fixture map, must carry these atoms."""
    repo_root = Path(__file__).resolve().parents[4]
    exclusions = load_update_exclusions(repo_root)
    assert set(exclusions) == set(EXCLUSIONS)
    assert all(reason.strip() for reason in exclusions.values())
