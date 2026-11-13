"""The one way Apiary runs git (and other short tools): never through a shell, never
waiting on a password prompt, output decoded as UTF-8 whatever the console code page."""
import os
import shutil
import subprocess

# Reads (status, rev-parse, diff) give up after this; writes go through jobs.py instead.
READ_TIMEOUT_S = 15

# No window flashing up for each git call when the UI runs from pythonw / a bat.
CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# The background reads (the status of every repo, again and again): no console at all
# (CREATE_NO_WINDOW still starts a conhost.exe per git), below-normal priority, so the
# machine stays responsive while 188 repos are read.
BACKGROUND_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class GitError(Exception):
    """git exited non-zero (or could not run): the message is its stderr, as git said it."""


def env():
    """The environment for every child: no credential / ssh prompt that would hang it,
    English messages so the page shows what people search for."""
    e = dict(os.environ)
    # Not GIT_SSH_COMMAND: it would override a user's core.sshCommand. ssh cannot ask
    # anyway - stdin is the null device and the child has no console window.
    e["GIT_TERMINAL_PROMPT"] = "0"
    e["LC_ALL"] = e["LANG"] = "C.UTF-8"
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def git_exe():
    return shutil.which("git")


def run(args, cwd, timeout=READ_TIMEOUT_S, check=True, input_text=None, rc=False, background=False):
    """git <args> in cwd -> stdout (str); rc: the exit code instead (never raises on it).
    check: GitError on a non-zero exit. background: a read nobody waits on (BACKGROUND_FLAGS,
    and GIT_OPTIONAL_LOCKS=0: status does not take index.lock to write the index back)."""
    e = env()
    if background:
        e["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args], cwd=cwd, env=e, capture_output=True,
            timeout=timeout, creationflags=BACKGROUND_FLAGS if background else CREATE_FLAGS,
            **({"input": input_text.encode("utf-8")} if input_text is not None else {"stdin": subprocess.DEVNULL}))
    except FileNotFoundError:
        raise GitError("找不到 git：请安装 Git for Windows 并加入 PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args[:2])} 超过 {timeout} s 没有返回") from None
    if rc:
        return proc.returncode
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or out.strip()
        raise GitError(err or f"git {' '.join(args[:2])} 退出码 {proc.returncode}")
    return out


def git_dirs(path):
    """-> (git dir, common dir) of the work tree at path, read from the files (no git
    process: on a guarded Windows box each one costs ~0.1 s); None when not found."""
    dot = os.path.join(path, ".git")
    if os.path.isdir(dot):
        gd = dot
    else:
        try:
            with open(dot, encoding="utf-8") as f:
                line = f.readline().strip()
        except OSError:
            return None
        if not line.startswith("gitdir: "):
            return None
        gd = os.path.normpath(os.path.join(path, line[len("gitdir: "):]))
    common = gd
    try:
        with open(os.path.join(gd, "commondir"), encoding="utf-8") as f:
            common = os.path.normpath(os.path.join(gd, f.readline().strip()))
    except OSError:
        pass
    return gd, common


def read_ref(path, ref):
    """The sha a branch ref (refs/heads/...) points at, from the files; None if none.
    A symbolic ref or anything odd: None too (the caller falls back to git)."""
    dirs = git_dirs(path)
    if not dirs:
        return None
    common = dirs[1]
    try:
        with open(os.path.join(common, *ref.split("/")), encoding="utf-8") as f:
            sha = f.readline().strip()
        return sha if len(sha) >= 40 and not sha.startswith("ref:") else None
    except OSError:
        pass
    try:
        with open(os.path.join(common, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return None


def is_repo_dir(path):
    """A git working tree's top: .git is a directory with a HEAD in it, or (worktree /
    submodule) a "gitdir: ..." file. A .git holding only a tool's leftovers (GitKraken's
    .git/gk) is not one."""
    dirs = git_dirs(path)
    return bool(dirs) and os.path.isfile(os.path.join(dirs[0], "HEAD"))
