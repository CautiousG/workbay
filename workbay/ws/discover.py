"""Find the workspaces: the settings' roots one level down, plus the added ones, minus
the hidden ones; tell each one's kind and list its repositories.

    west    .west/config: the manifest's projects (and the top, if it is a git repo too)
    single  the directory is a git repo
    multi   git repos below it (two levels at most; nothing under a repo is looked at)
"""
import os
from dataclasses import dataclass, field

from . import manifest as manifest_mod
from ..core.gitcmd import is_repo_dir

SKIP = {"$recycle.bin", "system volume information", "node_modules", ".venv", "venv", "__pycache__"}
MULTI_DEPTH = 2


def norm_id(path):
    """The key a workspace / path goes by: absolute, normalized, case folded (Windows)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


@dataclass
class Repo:
    name: str
    path: str                 # relative to the workspace ("." for the workspace itself)
    abspath: str
    role: str                 # project | manifest | top | plain
    declared: object = None   # manifest.Declared, west only
    cloned: bool = True

    @property
    def id(self):
        return norm_id(self.abspath)


@dataclass
class Workspace:
    name: str
    path: str
    kind: str                 # west | multi | single
    repos: list = field(default_factory=list)
    manifest: object = None   # manifest.ManifestInfo, west only
    error: str = ""

    @property
    def id(self):
        return norm_id(self.path)

    def repo(self, repo_id):
        return next((r for r in self.repos if r.id == repo_id), None)


def _skipped(name):
    return name.startswith(".") or name.lower() in SKIP


def _subdirs(path):
    try:
        with os.scandir(path) as it:
            return sorted((e for e in it if e.is_dir(follow_symlinks=False) and not _skipped(e.name)),
                          key=lambda e: e.name.lower())
    except OSError:
        return []


def find_repos(top, depth=MULTI_DEPTH):
    """The git repos under top (not top itself), depth levels at most, not inside a repo."""
    out = []
    for entry in _subdirs(top):
        if is_repo_dir(entry.path):
            out.append(entry.path)
        elif depth > 1:
            out += find_repos(entry.path, depth - 1)
    return out


def _plain(ws_path, abspath, role="plain"):
    rel = os.path.relpath(abspath, ws_path)
    return Repo(name=os.path.basename(abspath) if rel != "." else os.path.basename(ws_path) or ws_path,
                path=rel, abspath=abspath, role=role)


def probe(path):
    """path -> its Workspace, or None when it is none of the three kinds."""
    path = os.path.normpath(os.path.abspath(path))
    name = os.path.basename(path) or path
    if os.path.isfile(os.path.join(path, ".west", "config")):
        ws = Workspace(name, path, "west")
        try:
            ws.manifest = manifest_mod.load(path)
        except manifest_mod.ManifestError as exc:
            ws.error = f"manifest 读取失败，按普通目录列出：{exc}"
            ws.repos = [_plain(path, p) for p in find_repos(path)]
        else:
            for d in ws.manifest.projects:
                ws.repos.append(Repo(name=d.name, path=d.path, abspath=d.abspath,
                                     role="manifest" if d.is_manifest else "project",
                                     declared=d, cloned=is_repo_dir(d.abspath)))
        if is_repo_dir(path):
            ws.repos.insert(0, _plain(path, path, "top"))
        return ws
    if is_repo_dir(path):
        return Workspace(name, path, "single", [_plain(path, path, "top")])
    repos = find_repos(path)
    if repos:
        return Workspace(name, path, "multi", [_plain(path, p) for p in repos])
    return None


def candidates(settings):
    """The directories to probe, in order, each once, the hidden ones left out."""
    hidden = {norm_id(h) for h in settings.hidden}
    seen, out = set(), []
    paths = [e.path for root in settings.roots if os.path.isdir(root) for e in _subdirs(root)]
    for p in paths + list(settings.added):
        key = norm_id(p)
        if key in hidden or key in seen or not os.path.isdir(p):
            continue
        seen.add(key)
        out.append(p)
    return out


def scan(settings, executor=None):
    """The workspaces the settings point at, in candidates' order. executor: probe in
    parallel (manifests take ~0.2 s each)."""
    paths = candidates(settings)
    results = executor.map(probe, paths) if executor else map(probe, paths)
    return [ws for ws in results if ws]
