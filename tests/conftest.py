"""Shared fixtures: every test runs in its own tmp_path, with its own git identity,
its own APIARY_HOME, and git config that does not inherit the user's global ignore
rules (a global `*.txt` in core.excludesFile would silence half the fixtures)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Apiary Test",
    "GIT_AUTHOR_EMAIL": "apiary@example.invalid",
    "GIT_COMMITTER_NAME": "Apiary Test",
    "GIT_COMMITTER_EMAIL": "apiary@example.invalid",
}

WEST_YML = """\
manifest:
  projects:
    - name: projA
      url: {a}
      revision: main
      path: a
    - name: projB
      url: {b}
      revision: main
      path: b
      groups: [optional]
  group-filter: [-optional]
  self:
    path: manifest
"""


# --- helpers the tests import ------------------------------------------------------

def git(cwd, *args):
    """git <args> in cwd -> stdout (str); a non-zero exit raises."""
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)
    return proc.stdout.decode("utf-8", "replace")


def configure_repo(path):
    """Local config that makes a repo independent of the machine's global settings."""
    path = Path(path)
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "core.autocrlf", "false")
    ignore = path / ".git" / "apiary-empty-ignore"
    ignore.write_text("", encoding="utf-8")
    git(path, "config", "core.excludesFile", str(ignore))


def init_repo(path, branch="main"):
    """A fresh repository at path with the test config, on branch."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    configure_repo(path)
    return path


def commit_all(path, message):
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)


def run_west(cwd, *args):
    proc = subprocess.run([sys.executable, "-m", "west", *args], cwd=str(cwd), capture_output=True)
    assert proc.returncode == 0, (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return proc.stdout.decode("utf-8", "replace")


def make_repo_with_remote(base, name="solo", clone_path=None):
    """A bare repo, a seed repo that pushed to it, and a clone of it -> (bare, seed, clone)."""
    base = Path(base)
    seed = init_repo(base / f"{name}-seed")
    (seed / "readme.md").write_text(f"{name}\n", encoding="utf-8")
    (seed / "file.txt").write_text("one\n", encoding="utf-8")
    commit_all(seed, f"{name} initial")
    bare = base / f"{name}.git"
    git(base, "clone", "-q", "--bare", str(seed), str(bare))
    clone = Path(clone_path) if clone_path else base / f"{name}-clone"
    git(base, "clone", "-q", str(bare), str(clone))
    configure_repo(clone)
    return bare, seed, clone


def make_west_ws(ws_path, remotes_path):
    """A minimal west workspace at ws_path (its bare repos live in remotes_path)."""
    ws_path, remotes_path = Path(ws_path), Path(remotes_path)
    remotes_path.mkdir(parents=True, exist_ok=True)
    urls = {}
    for name in ("projA", "projB"):
        seed = init_repo(remotes_path / f"{name}-seed")
        (seed / "readme.md").write_text(f"{name}\n", encoding="utf-8")
        commit_all(seed, f"{name} initial")
        bare = remotes_path / f"{name}.git"
        git(remotes_path, "clone", "-q", "--bare", str(seed), str(bare))
        urls[name] = bare.as_posix()
    manifest = init_repo(ws_path / "manifest")
    (manifest / "west.yml").write_text(WEST_YML.format(a=urls["projA"], b=urls["projB"]), encoding="utf-8")
    commit_all(manifest, "manifest")
    run_west(ws_path, "init", "-l", "manifest")
    run_west(ws_path, "update")
    # west's own clones are local configs of their own: give them ours too.
    for cloned in (ws_path / "a", ws_path / "b"):
        if (cloned / ".git").exists():
            configure_repo(cloned)
    return ws_path


# --- fixtures ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def apiary_home(tmp_path, monkeypatch):
    """Never read or write the user's real %APPDATA%/apiary/settings.json."""
    home = tmp_path / "apiary-home"
    home.mkdir()
    monkeypatch.setenv("APIARY_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for key, value in GIT_IDENTITY.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def remote_and_clone(tmp_path):
    """-> (bare remote.git, work clone of it, seed repo that pushed main)."""
    base = tmp_path / "rc"
    base.mkdir()
    remote = base / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True, capture_output=True)
    seed = init_repo(base / "seed")
    (seed / "file.txt").write_text("one\n", encoding="utf-8")
    commit_all(seed, "c1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")
    work = base / "work"
    git(base, "clone", "-q", str(remote), str(work))
    configure_repo(work)
    return remote, work, seed


@pytest.fixture
def west_ws(tmp_path):
    """A west workspace with projA cloned (path a) and projB filtered out (path b)."""
    return make_west_ws(tmp_path / "ws", tmp_path / "remotes")


@pytest.fixture
def api_env(tmp_path):
    """An Api over a root holding one single repo and one west workspace."""
    from apiary.config import Settings
    from apiary.ui.api import Api

    root = tmp_path / "root"
    root.mkdir()
    _, _, single = make_repo_with_remote(tmp_path / "solo", "solo", clone_path=root / "single")
    make_west_ws(root / "westws", tmp_path / "west-remotes")
    api = Api(settings=Settings(roots=[str(root)]), save=False)
    try:
        yield api, root, root / "single"
    finally:
        api.close()
