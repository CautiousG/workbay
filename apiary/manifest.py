"""A west workspace's manifest, read only, through west's own API (so imports, group
filters and the workspace's config are what `west list` would make of them).

Writes (update, forall) never come here: they run the west CLI, see westcli.py."""
import contextlib
import io
import os
import threading
from dataclasses import dataclass, field

# west's schema check (pykwalify) is not thread safe: two manifests validated at once
# make each other fail ("Malformed manifest", YAML errors in west's own schema file).
# So one load at a time; they take ~0.2 s each.
_LOAD_LOCK = threading.Lock()


class ManifestError(Exception):
    """west could not load the manifest: the message is west's."""


@dataclass
class Declared:
    """A project as the manifest has it."""
    name: str
    path: str               # relative to the workspace, the OS's separators
    abspath: str
    revision: str
    url: str
    groups: list
    active: bool            # group filter (manifest + .west/config) lets it in
    is_manifest: bool = False
    remote_name: str = "origin"   # the git remote west sets up in the clone


@dataclass
class ManifestInfo:
    file: str               # the manifest file in use, absolute
    repo: str               # the manifest repository, absolute
    group_filter: list
    projects: list = field(default_factory=list)   # [Declared], the manifest repo first

    def files(self):
        """The yml files of the manifest repository (the Raw tab's choices), relative."""
        out = []
        for base, dirs, names in os.walk(self.repo):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            out += [os.path.relpath(os.path.join(base, n), self.repo)
                    for n in names if n.endswith((".yml", ".yaml"))]
        return sorted(out, key=str.lower)


def load(topdir):
    """topdir's manifest -> ManifestInfo. ManifestError when west cannot load it."""
    try:
        from west.manifest import Manifest
    except ImportError as exc:                        # pragma: no cover - a dependency
        raise ManifestError(f"没有 west 模块：pip install west ({exc})") from None
    noise = io.StringIO()                             # pykwalify prints to stdout on errors
    try:
        with _LOAD_LOCK, contextlib.redirect_stdout(noise):
            m = Manifest.from_topdir(topdir=os.fspath(topdir))
    except Exception as exc:                          # west raises many kinds
        raise ManifestError(f"{type(exc).__name__}: {exc}") from None
    info = ManifestInfo(file=os.path.normpath(m.path), repo=os.path.normpath(m.repo_abspath),
                        group_filter=list(m.group_filter or []))
    for i, p in enumerate(m.projects):
        info.projects.append(Declared(
            name=p.name, path=os.path.normpath(p.path), abspath=os.path.normpath(p.abspath),
            revision=p.revision or "", url=p.url or "", groups=list(p.groups or []),
            active=True if i == 0 else bool(m.is_active(p)), is_manifest=(i == 0),
            remote_name=getattr(p, "remote_name", None) or "origin"))
    return info
