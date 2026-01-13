from __future__ import annotations

from pathlib import Path

from overlay_tools.core.subprocess_utils import run


def is_git_repo(path: Path) -> bool:
    try:
        result = run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            check=False,
            capture=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def git_root(path: Path) -> Path:
    result = run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        check=True,
        capture=True,
    )
    return Path(result.stdout.strip())


def git_current_branch(repo_root: Path) -> str:
    result = run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        check=True,
        capture=True,
    )
    return result.stdout.strip()


def git_default_branch(repo_root: Path) -> str:
    result = run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
        check=False,
        capture=True,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        return ref.replace("refs/remotes/origin/", "")
    return "master"


def git_checkout_branch(
    branch: str,
    repo_root: Path,
    *,
    create: bool = False,
    start_point: str | None = None,
) -> None:
    cmd = ["git", "checkout"]
    if create:
        cmd.append("-b")
    cmd.append(branch)
    if start_point:
        cmd.append(start_point)
    run(cmd, cwd=repo_root, check=True)


def git_branch_exists(branch: str, repo_root: Path, *, remote: bool = False) -> bool:
    if remote:
        result = run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=repo_root,
            check=False,
            capture=True,
        )
        return bool(result.stdout.strip())
    else:
        result = run(
            ["git", "rev-parse", "--verify", branch],
            cwd=repo_root,
            check=False,
            capture=True,
        )
        return result.returncode == 0


def git_push(
    repo_root: Path,
    *,
    remote: str = "origin",
    branch: str | None = None,
    set_upstream: bool = True,
    force: bool = False,
) -> None:
    cmd = ["git", "push"]
    if set_upstream:
        cmd.append("-u")
    if force:
        cmd.append("--force-with-lease")
    cmd.append(remote)
    if branch:
        cmd.append(branch)
    run(cmd, cwd=repo_root, check=True)


def git_add(paths: list[Path], repo_root: Path) -> None:
    if not paths:
        return
    run(["git", "add", "--"] + [str(p) for p in paths], cwd=repo_root, check=True)


def git_commit(message: str, repo_root: Path) -> None:
    run(["git", "commit", "-m", message], cwd=repo_root, check=True)


def git_status(repo_root: Path) -> str:
    result = run(["git", "status", "--porcelain"], cwd=repo_root, check=True)
    return result.stdout


def git_has_changes(repo_root: Path) -> bool:
    return bool(git_status(repo_root).strip())


def git_config_user(repo_root: Path, name: str, email: str) -> None:
    run(["git", "config", "user.name", name], cwd=repo_root, check=True)
    run(["git", "config", "user.email", email], cwd=repo_root, check=True)
