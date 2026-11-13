"""Bring one repository to what the manifest says (sync), or force it there (align).

A Job step runs it as `python sync.py sync|align --remote R --revision REV [--url U]
[--autostash]` in the repository, so the Job's output, cancel and workspace lock apply;
the tests call sync() / align() directly.

    sync   fetch the revision once, then: a branch -> switch to the local branch of that
           name (made from the remote one when missing) and rebase it onto the remote
           one; a tag / commit -> detach there. Anything that fails puts the repository
           back as it was. Uncommitted changes: skipped, or stashed around it (autostash).
    align  "I do not care about this one": abort what is half done, stash the changes,
           keep the old HEAD under refs/workbay/backup/, reset the branch to the remote.

Exit code: 0 done, 1 failed, 2 skipped (nothing touched). The last line is
"@@note <text>", the one-line outcome the page shows next to the step.

Standard library only: it runs as a script, outside the package.
"""
import argparse
import os
import re
import subprocess
import sys
import time

OK, FAILED, SKIPPED = 0, 1, 2
BACKUP_PREFIX = "refs/workbay/backup/"
NOTE = "@@note "
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# What a half-done operation leaves in the git dir -> the operation (as gitstat.py).
_IN_PROGRESS = (("rebase-merge", "rebase"), ("rebase-apply/applying", "am"), ("rebase-apply", "rebase"),
                ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))


class Outcome(Exception):
    """Stop here with this exit code and note."""

    def __init__(self, code, note):
        super().__init__(note)
        self.code, self.note = code, note


class Git:
    """git in one repository, every command and its output printed (the Job's log)."""

    def __init__(self, cwd, out=print):
        self.cwd, self.out = cwd, out

    def __call__(self, *args, check=True, show=True):
        if show:
            self.out("$ git " + " ".join(args))
        proc = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=self.cwd, capture_output=True,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace").rstrip()
        if show and text:
            self.out(text)
        if check and proc.returncode:
            raise Outcome(FAILED, _first_error(text) or f"git {args[0]} 失败")
        return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()

    def value(self, *args):
        """stdout of a quiet git, "" when it fails."""
        rc, out = self(*args, check=False, show=False)
        return "" if rc else out


def _first_error(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return next((l for l in lines if l.lower().startswith(("error", "fatal"))), lines[-1] if lines else "")


def in_progress(g):
    git_dir = g.value("rev-parse", "--absolute-git-dir")
    return next((what for name, what in _IN_PROGRESS if git_dir and os.path.exists(os.path.join(git_dir, name))), None)


def _dirty(g):
    """How many tracked files have changes (untracked ones do not count)."""
    return sum(1 for l in g.value("status", "--porcelain", "--untracked-files=no").splitlines() if l.strip())


def _head(g):
    return g.value("symbolic-ref", "-q", "--short", "HEAD") or None, g.value("rev-parse", "HEAD")


def _ensure_remote(g, remote, url):
    if remote not in g.value("remote").split() and url:
        g("remote", "add", "--", remote, url)


def fetch(g, remote, revision):
    """Fetch revision once -> (kind branch | tag | commit, name, sha)."""
    rev = revision.removeprefix("refs/heads/")
    if rev.startswith("refs/tags/"):
        rev = rev[len("refs/tags/"):]
    elif _SHA.match(rev):
        if g("cat-file", "-e", rev + "^{commit}", check=False, show=False)[0]:
            g("fetch", remote, rev)
        return "commit", rev, g.value("rev-parse", rev + "^{commit}")
    else:
        if g("fetch", remote, f"+refs/heads/{rev}:refs/remotes/{remote}/{rev}", check=False)[0] == 0:
            return "branch", rev, g.value("rev-parse", f"refs/remotes/{remote}/{rev}")
    if g("fetch", remote, f"+refs/tags/{rev}:refs/tags/{rev}", check=False)[0] == 0:
        return "tag", rev, g.value("rev-parse", f"refs/tags/{rev}^{{commit}}")
    raise Outcome(FAILED, f"fetch 失败：{remote} 上没有 {revision}，或连不上")


def _stash(g, why):
    g("stash", "push", "-m", f"workbay {why} {time.strftime('%Y-%m-%d %H:%M:%S')}")


def _unstash(g):
    """Put the stashed changes back -> "" or why they stay in the stash."""
    if g("stash", "pop", check=False)[0] == 0:
        return ""
    g("reset", "-q", "--hard", "HEAD")       # only the half-applied stash: it was clean before
    return "改动与新代码冲突，留在 stash@{0} 里"


def _backup(g, sha):
    ref = f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{sha[:8]}"
    g("update-ref", ref, sha)
    return ref[len("refs/"):]


def _backup_detached(g, orig, target):
    """A detached HEAD with commits of its own: nothing would name them after the
    checkout, so they get a backup ref -> its name, or ""."""
    branch, head = orig
    if branch or not head or g("merge-base", "--is-ancestor", head, target, check=False, show=False)[0] == 0:
        return ""
    if g.value("for-each-ref", "--count=1", "--contains", head, "refs/heads/", "refs/remotes/"):
        return ""                             # on some branch already
    return _backup(g, head)


def _rollback(g, orig):
    op = in_progress(g)
    if op:
        g(*_ABORT[op], check=False)
    g("checkout", "-q", orig[0] or orig[1], check=False)


_ABORT = {"rebase": ("rebase", "--abort"), "am": ("am", "--abort"), "merge": ("merge", "--abort"),
          "cherry-pick": ("cherry-pick", "--abort"), "revert": ("revert", "--abort")}


def sync(path, remote, revision, url="", autostash=False, out=print):
    """-> (exit code, note)."""
    g = Git(path, out)
    try:
        op = in_progress(g)
        if op:
            return SKIPPED, f"{op} 进行中，先解除卡住"
        _ensure_remote(g, remote, url)
        kind, name, target = fetch(g, remote, revision)
        g("update-ref", "refs/heads/manifest-rev", target, show=False)
        dirty = _dirty(g)
        if dirty and not autostash:
            return SKIPPED, f"有 {dirty} 个文件未提交，跳过（勾选 autostash 可带上）"
        orig = _head(g)
        kept = _backup_detached(g, orig, target)
        if dirty:
            _stash(g, "sync")
        try:
            if kind == "branch":
                if g.value("rev-parse", "--verify", "-q", f"refs/heads/{name}"):
                    if orig[0] != name:
                        g("checkout", "-q", name)
                else:
                    g("checkout", "-q", "-b", name, "--track", f"{remote}/{name}")
                if not g.value("rev-parse", "--abbrev-ref", f"{name}@{{upstream}}"):
                    g("branch", "-q", f"--set-upstream-to={remote}/{name}", name)
                if g("rebase", f"{remote}/{name}", check=False)[0]:
                    raise Outcome(FAILED, "rebase 冲突，已回滚到同步前")
                ahead = g.value("rev-list", "--count", f"{remote}/{name}..HEAD")
                note = f"{name} = {remote}/{name}" if ahead in ("", "0") else f"{name}：{ahead} 个本地 commit 在 {remote}/{name} 之上"
            else:
                g("checkout", "-q", "--detach", target)
                note = f"{kind} {name}（detached {target[:10]}）"
        except Outcome as exc:
            _rollback(g, orig)
            if dirty:
                _unstash(g)
            return FAILED, exc.note
        if kept:
            note += f"；原 detached {orig[1][:10]} 备份为 {kept}"
        if dirty:
            left = _unstash(g)
            note += "；" + (left or "改动已带回")
        return OK, note
    except Outcome as exc:
        return exc.code, exc.note


def align(path, remote, revision, url="", out=print):
    """-> (exit code, note). Nothing is thrown away: changes go to the stash, the old
    HEAD (and the old branch, when it moves) to refs/workbay/backup/."""
    g = Git(path, out)
    notes = []
    try:
        op = in_progress(g)
        if op:
            g(*_ABORT[op], check=False)
            notes.append(f"已放弃 {op}")
        if _dirty(g):
            _stash(g, "align")
            notes.append("改动在 stash@{0}")
        _ensure_remote(g, remote, url)
        kind, name, target = fetch(g, remote, revision)
        g("update-ref", "refs/heads/manifest-rev", target, show=False)
        branch, head = _head(g)
        old = {head} | ({g.value("rev-parse", f"refs/heads/{name}")} if kind == "branch" else set())
        for sha in sorted(o for o in old if o):
            if g("merge-base", "--is-ancestor", sha, target, check=False, show=False)[0]:
                notes.append(f"原 {sha[:10]} 备份为 {_backup(g, sha)}")
        if kind == "branch":
            g("checkout", "-q", "-B", name, f"{remote}/{name}")
            g("branch", "-q", f"--set-upstream-to={remote}/{name}", name)
            notes.insert(0, f"{name} = {remote}/{name}")
        else:
            g("checkout", "-q", "--detach", target)
            notes.insert(0, f"{kind} {name}（detached {target[:10]}）")
        return OK, "；".join(notes)
    except Outcome as exc:
        return exc.code, "；".join([exc.note] + notes)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("sync", "align"))
    p.add_argument("--remote", default="origin")
    p.add_argument("--revision", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--autostash", action="store_true")
    a = p.parse_args(argv)
    if a.action == "sync":
        code, note = sync(os.getcwd(), a.remote, a.revision, a.url, a.autostash)
    else:
        code, note = align(os.getcwd(), a.remote, a.revision, a.url)
    print(NOTE + note, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
