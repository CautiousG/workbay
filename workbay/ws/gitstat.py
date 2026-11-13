"""A repository's state from one `git status --porcelain=v2 --branch` (and, for a west
project, its manifest-rev): local only, no network."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from ..core import gitcmd

WORKERS = 16
# git processes reading status at once, over every request (the page reads three
# workspaces side by side, each with its own pool): a third of the cores, 4..8.
SLOTS = max(4, min(8, (os.cpu_count() or 8) // 3))
_slots = threading.BoundedSemaphore(SLOTS)


@dataclass
class RepoStatus:
    branch: str = None        # None: detached
    head: str = ""            # short sha; "" before the first commit
    upstream: str = None
    ahead: int = None         # None: no upstream
    behind: int = None
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicted: int = 0
    manifest_rev: str = None  # short sha of refs/heads/manifest-rev (west projects)
    on_manifest: bool = None  # HEAD == manifest-rev, or ahead of it; None when there is none
    manifest_ahead: bool = False   # HEAD is a descendant of manifest-rev (moved on, nothing lost)
    fetched_at: float = None  # mtime of FETCH_HEAD
    in_progress: str = None   # rebase | merge | cherry-pick | revert | am left half done
    error: str = ""

    @property
    def dirty(self):
        return bool(self.staged or self.modified or self.conflicted)

    def as_dict(self):
        d = asdict(self)
        d["dirty"] = self.dirty
        return d


def parse_porcelain(text, status=None):
    """`git status --porcelain=v2 --branch` -> RepoStatus (counts and branch fields)."""
    st = status or RepoStatus()
    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split()[2]
            st.head = "" if oid == "(initial)" else oid[:10]
        elif line.startswith("# branch.head "):
            head = line.split(maxsplit=2)[2]
            st.branch = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            st.upstream = line.split(maxsplit=2)[2]
        elif line.startswith("# branch.ab "):
            _, _, a, b = line.split()
            st.ahead, st.behind = int(a), -int(b)
        elif line.startswith(("1 ", "2 ")):
            xy = line[2:4]
            st.staged += xy[0] != "."
            st.modified += xy[1] != "."
        elif line.startswith("u "):
            st.conflicted += 1
        elif line.startswith("? "):
            st.untracked += 1
    return st


# What a half-done operation leaves in the git dir, in the order git itself checks.
_IN_PROGRESS = (("rebase-merge", "rebase"), ("rebase-apply/applying", "am"), ("rebase-apply", "rebase"),
                ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))


def in_progress(path):
    """The operation git is in the middle of at path (from the git dir's files), or None."""
    dirs = gitcmd.git_dirs(path)
    if not dirs:
        return None
    return next((what for name, what in _IN_PROGRESS if os.path.exists(os.path.join(dirs[0], name))), None)


def _fetched_at(path):
    dirs = gitcmd.git_dirs(path)
    try:
        return os.path.getmtime(os.path.join(dirs[0], "FETCH_HEAD")) if dirs else None
    except OSError:
        return None


def status(path, manifest_rev=False):
    """path's RepoStatus; a failure is in .error, never raised. One git process: the
    rest is read from .git's files."""
    st = RepoStatus()
    try:
        with _slots:
            out = gitcmd.run(["status", "--porcelain=v2", "--branch"], path, background=True)
        parse_porcelain(out, st)
    except gitcmd.GitError as exc:
        st.error = str(exc)
        return st
    if manifest_rev:
        rev = gitcmd.read_ref(path, "refs/heads/manifest-rev")
        if rev:
            st.manifest_rev = rev[:10]
            st.on_manifest = bool(st.head) and rev.startswith(st.head)
            if not st.on_manifest and st.head:
                # one more git only for the ones that differ: pulled on past it is fine
                with _slots:
                    ahead = gitcmd.run(["merge-base", "--is-ancestor", rev, "HEAD"], path, rc=True, background=True) == 0
                st.manifest_ahead = st.on_manifest = ahead
    st.fetched_at = _fetched_at(path)
    st.in_progress = in_progress(path)
    return st


def statuses(repos, workers=WORKERS):
    """{repo.id: RepoStatus} for the cloned ones of repos (discover.Repo), in parallel (the
    git processes of all calls together: SLOTS at most)."""
    todo = [r for r in repos if r.cloned]
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, SLOTS, len(todo))) as pool:
        results = pool.map(lambda r: status(r.abspath, manifest_rev=r.role == "project"), todo)
        return {r.id: s for r, s in zip(todo, results)}
