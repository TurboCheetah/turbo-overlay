"""Run an exact overlay ebuild through Portage phases in a disposable Docker image."""

import argparse
import re
import subprocess
from pathlib import Path

IMAGE = "turbo-overlay/ebuild-test:local"
EBUILD_PATH = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+\.ebuild$")
IMAGE_PATH = re.compile(r"^[A-Za-z0-9+_.-]+(/[A-Za-z0-9+_.-]+)*$")
TOOLS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OVERLAY = TOOLS_ROOT.parents[2]


def validate_ebuild(overlay: Path, path: str) -> None:
    if not EBUILD_PATH.fullmatch(path):
        raise ValueError("expected category/package/package-version.ebuild")
    if not (overlay / "profiles/repo_name").is_file():
        raise ValueError(f"not a Gentoo overlay: {overlay}")
    ebuild = overlay / path
    if not ebuild.is_file() or not ebuild.resolve().is_relative_to(overlay):
        raise ValueError(f"ebuild missing or outside overlay: {path}")
    category, package, filename = path.split("/")
    if not filename.startswith(f"{package}-") or not category:
        raise ValueError(f"ebuild filename does not match package: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ebuild", help="exact category/package/package-version.ebuild")
    parser.add_argument("--overlay-path", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--build", action="store_true", help="build/refresh the Docker image")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="IMAGE_PATH",
        help="require a relative path in the Portage install image (repeatable)",
    )
    parser.add_argument(
        "--image", default=IMAGE, help="Docker image tag (default: local test image)"
    )
    args = parser.parse_args(argv)
    overlay = args.overlay_path.resolve()
    try:
        validate_ebuild(overlay, args.ebuild)
        for path in args.expect:
            if not IMAGE_PATH.fullmatch(path) or any(
                part in {".", ".."} for part in path.split("/")
            ):
                raise ValueError(f"expected install-image path must be relative: {path}")
    except ValueError as exc:
        parser.error(str(exc))

    if args.build:
        context = TOOLS_ROOT / "docker"
        subprocess.run(["docker", "build", "-t", args.image, str(context)], check=True)

    cmd = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--mount",
        f"type=bind,src={overlay},dst=/var/db/repos/turbo-overlay,readonly",
        args.image,
        args.ebuild,
        *args.expect,
    ]
    try:
        return subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        parser.error("Docker is not installed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
