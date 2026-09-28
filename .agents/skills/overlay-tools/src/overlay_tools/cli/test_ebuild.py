"""Run an exact overlay ebuild through Portage phases in a disposable Docker image."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from overlay_tools.core.errors import ExternalToolMissingError
from overlay_tools.core.subprocess_utils import require_tool, run

DOCKER_TAG = "turbo-overlay/ebuild-test:local"
DOCKER_PLATFORM = ["--platform", "linux/amd64"]
CONTAINER_REPO = "/var/db/repos/turbo-overlay"
EBUILD_PATH = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+\.ebuild$")
STAGED_PATH = re.compile(r"^[A-Za-z0-9+_.-]+(/[A-Za-z0-9+_.-]+)*$")
TOOLS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OVERLAY = TOOLS_ROOT.parents[2]


def validate_ebuild(overlay: Path, path: str) -> None:
    if not EBUILD_PATH.fullmatch(path):
        raise ValueError("expected category/package/package-version.ebuild")
    if "," in str(overlay):
        raise ValueError(f"overlay path cannot contain a comma: {overlay}")
    if not (overlay / "profiles/repo_name").is_file():
        raise ValueError(f"not a Gentoo overlay: {overlay}")
    ebuild = overlay / path
    if not ebuild.is_file() or not ebuild.resolve().is_relative_to(overlay):
        raise ValueError(f"ebuild missing or outside overlay: {path}")
    _, package, filename = path.split("/")
    if not filename.startswith(f"{package}-"):
        raise ValueError(f"ebuild filename does not match package: {path}")


def validate_staged_path(path: str) -> None:
    if not STAGED_PATH.fullmatch(path) or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError(f"staged path must be relative: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ebuild", help="exact category/package/package-version.ebuild")
    parser.add_argument("--overlay-path", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--build", action="store_true", help="build/refresh the Docker image")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="STAGED_PATH",
        help="require a relative path in the Portage install image (repeatable)",
    )
    args = parser.parse_args(argv)
    overlay = args.overlay_path.resolve()
    try:
        validate_ebuild(overlay, args.ebuild)
        for path in args.expect:
            validate_staged_path(path)
        require_tool("docker", "https://docs.docker.com/engine/install/")
    except (ValueError, ExternalToolMissingError) as exc:
        parser.error(str(exc))

    if args.build:
        context = TOOLS_ROOT / "docker"
        build = ["docker", "build", "--pull", "--no-cache", *DOCKER_PLATFORM, "-t", DOCKER_TAG]
        result = run([*build, str(context)], check=False, capture=False)
        if result.returncode != 0:
            parser.error(f"docker build failed with exit code {result.returncode}")

    cmd = [
        "docker",
        "run",
        "--rm",
        *DOCKER_PLATFORM,
        "--mount",
        f"type=bind,src={overlay},dst={CONTAINER_REPO},readonly",
        "--env",
        f"OVERLAY_REPO={CONTAINER_REPO}",
        DOCKER_TAG,
        args.ebuild,
        *args.expect,
    ]
    return run(cmd, check=False, capture=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
