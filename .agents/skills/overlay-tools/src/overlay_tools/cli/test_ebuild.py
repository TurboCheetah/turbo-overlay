"""Run an exact overlay ebuild through Portage phases in a disposable Docker image."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from overlay_tools.core.ebuilds import parse_ebuild_filename
from overlay_tools.core.errors import EbuildParseError, ExternalToolMissingError
from overlay_tools.core.logging import Logger
from overlay_tools.core.overlay import find_overlay_root
from overlay_tools.core.staged_assertions import parse_specs
from overlay_tools.core.subprocess_utils import require_tool, run

# 1 is reserved for ebuild phase/assertion failures, so an environment that
# cannot run the test gets a distinct code.
EXIT_ENVIRONMENT = 2
DOCKER_TAG = "turbo-overlay/ebuild-test:local"
DOCKER_PLATFORM = ["--platform", "linux/amd64"]
CONTAINER_REPO = "/var/db/repos/turbo-overlay"
CONTAINER_SCRIPT = "/usr/local/bin/run-ebuild"
EBUILD_PATH = re.compile(r"^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+\.ebuild$")
STAGED_PATH = re.compile(r"^[A-Za-z0-9+_.-]+(/[A-Za-z0-9+_.-]+)*$")
TOOLS_ROOT = Path(__file__).resolve().parents[3]
ASSERTION_OPTIONS = {
    "executable": "PATH",
    "type": "TYPE:PATH",
    "mode": "MODE:PATH",
    "link-target": "PATH=TARGET",
    "resolved-link": "PATH",
}


def bind_mount(src: Path, dst: str) -> str:
    # docker --mount parses its value as CSV, so these would split or quote the spec.
    if any(char in str(src) for char in ',"'):
        raise ValueError(f"mount path cannot contain a comma or double quote: {src}")
    return f"type=bind,src={src},dst={dst},readonly"


def validate_overlay(overlay: Path) -> None:
    if not (overlay / "profiles/repo_name").is_file():
        raise ValueError(f"not a Gentoo overlay: {overlay}")


def validate_ebuild(overlay: Path, path: str) -> None:
    if not EBUILD_PATH.fullmatch(path) or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("expected category/package/package-version.ebuild")
    ebuild = overlay / path
    if not ebuild.is_file() or not ebuild.resolve().is_relative_to(overlay):
        raise ValueError(f"ebuild missing or outside overlay: {path}")
    _, package, filename = path.split("/")
    try:
        name = parse_ebuild_filename(filename)
    except EbuildParseError as exc:
        raise ValueError(str(exc)) from exc
    if name.pn != package:
        raise ValueError(f"ebuild filename does not match package: {path}")


def validate_staged_path(path: str) -> None:
    if not STAGED_PATH.fullmatch(path) or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError(f"staged path must be relative: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ebuild", help="exact category/package/package-version.ebuild")
    parser.add_argument(
        "--overlay-path", type=Path, help="overlay checkout to mount (default: this repo)"
    )
    parser.add_argument("--build", action="store_true", help="build/refresh the Docker image")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="STAGED_PATH",
        help="require a relative path in the Portage install image (repeatable)",
    )
    for option, metavar in ASSERTION_OPTIONS.items():
        parser.add_argument(f"--expect-{option}", action="append", default=[], metavar=metavar)
    parser.add_argument(
        "--package-checks",
        action="store_true",
        help="add builtin checks for this exact atom from metadata/test-assertions.json",
    )
    args = parser.parse_args(argv)
    overlay = (args.overlay_path or find_overlay_root(TOOLS_ROOT) or TOOLS_ROOT).resolve()
    try:
        validate_overlay(overlay)
        validate_ebuild(overlay, args.ebuild)
        for path in args.expect:
            validate_staged_path(path)
        specs = [token for path in args.expect for token in ("--expect", path)]
        # JSON avoids confusing a legacy filename such as --expect-mode with an option.
        strong = any(path.startswith("--") for path in args.expect)
        for option in ASSERTION_OPTIONS:
            for value in getattr(args, f"expect_{option.replace('-', '_')}"):
                specs.extend([f"--expect-{option}", value])
                strong = True
        if args.package_checks:
            strong = True
            specs.append("--package-checks")
        checks = parse_specs(
            specs, ebuild=args.ebuild, registry=overlay / "metadata/test-assertions.json"
        )
        runner_args = ["--", "--assertions-json", json.dumps(checks)] if strong else args.expect
        mounts = [
            bind_mount(overlay, CONTAINER_REPO),
            bind_mount(TOOLS_ROOT / "docker/run-ebuild", CONTAINER_SCRIPT),
        ]
        if strong:
            mounts.append(
                bind_mount(
                    TOOLS_ROOT / "src/overlay_tools/core/staged_assertions.py",
                    "/usr/local/bin/staged-assertions.py",
                )
            )
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))

    log = Logger()
    try:
        require_tool("docker", "https://docs.docker.com/engine/install/")
    except ExternalToolMissingError as exc:
        log.error(str(exc))
        return EXIT_ENVIRONMENT

    if args.build:
        context = TOOLS_ROOT / "docker"
        build = ["docker", "build", "--pull", "--no-cache", *DOCKER_PLATFORM, "-t", DOCKER_TAG]
        result = run([*build, str(context)], check=False, capture=False)
        if result.returncode != 0:
            log.error(f"docker build failed with exit code {result.returncode}")
            return EXIT_ENVIRONMENT
    else:
        # Without this, docker run would try to pull the local-only tag from a registry.
        result = run(["docker", "image", "inspect", DOCKER_TAG], check=False)
        if result.returncode != 0:
            log.error(f"Docker image {DOCKER_TAG} unavailable (rerun with --build)")
            if result.stderr.strip():
                log.error(result.stderr.strip())
            return EXIT_ENVIRONMENT

    cmd = [
        "docker",
        "run",
        "--rm",
        *DOCKER_PLATFORM,
        *(arg for mount in mounts for arg in ("--mount", mount)),
        "--env",
        f"OVERLAY_REPO={CONTAINER_REPO}",
        DOCKER_TAG,
        args.ebuild,
        *runner_args,
    ]
    return run(cmd, check=False, capture=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
