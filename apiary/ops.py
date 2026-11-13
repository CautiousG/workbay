"""What the buttons do. Reads (repo detail, diff, update check) answer at once; writes
that take a while become jobs.Step lists for jobs.Jobs; quick writes (stage, commit)
run here. Everything checks the repository as it is now, never the page's copy."""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from . import gitcmd, westcli
from .gitstat import in_progress, parse_porcelain
from .jobs import Step

MAX_DIFF = 400_000
MAX_CHANGES = 2000
LOG_COUNT = 10


class OpError(Exception):
    """A refused or failed operation; the message is for the page."""


# --- the repo drawer ---------------------------------------------------------------

def changes(path):
    """[{path, orig, x, y, staged, unstaged, untracked, conflicted, add, del}] of the work tree,
    MAX_CHANGES at most. An untracked directory is one entry (git's default), as is a
    nested repository (a west project inside another)."""
    out = []
    for line in gitcmd.run(["status", "--porcelain=v2"], path).splitlines()[:MAX_CHANGES]:
        kind = line[:1]
        if kind == "1":
            xy, name, orig = line[2:4], line.split(" ", 8)[8], None
        elif kind == "2":
            xy = line[2:4]
            name, orig = line.split(" ", 9)[9].split("\t", 1)
        elif kind == "u":
            xy, name, orig = line[2:4], line.split(" ", 10)[10], None
        elif kind == "?":
            xy, name, orig = "??", line[2:], None
        else:
            continue
        out.append({"path": name, "orig": orig, "x": xy[0], "y": xy[1],
                    "staged": kind in "12" and xy[0] != ".", "unstaged": kind in "12" and xy[1] != ".",
                    "untracked": kind == "?", "conflicted": kind == "u", "add": None, "del": None})
    by_path = {c["path"]: c for c in out}
    for cached in (["--cached"], []):
        for line in gitcmd.run(["diff", "--numstat", *cached], path, check=False).splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in by_path:
                c = by_path[parts[2]]
                for key, val in (("add", parts[0]), ("del", parts[1])):
                    if val.isdigit():
                        c[key] = (c[key] or 0) + int(val)
    return out


def _refs(decoration):
    """%D ("HEAD -> main, origin/main, tag: v1") -> [{name, kind}], kind: head | branch |
    remote | tag | manifest."""
    out = []
    for part in filter(None, (p.strip() for p in decoration.split(","))):
        if part.startswith("HEAD -> "):
            out.append({"name": part[8:], "kind": "head"})
        elif part == "HEAD":
            out.append({"name": "HEAD", "kind": "head"})
        elif part.startswith("tag: "):
            out.append({"name": part[5:], "kind": "tag"})
        elif part == "manifest-rev":
            out.append({"name": part, "kind": "manifest"})
        elif part.endswith("/HEAD"):
            continue                                   # origin/HEAD: says nothing here
        else:
            out.append({"name": part, "kind": "remote" if "/" in part and not part.startswith("refs/heads/") else "branch"})
    return out


def _log_rows(text):
    rows = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            rows.append({"sha": parts[0], "subject": parts[1], "author": parts[2], "time": int(parts[3]),
                         "refs": _refs(parts[4])})
    return rows


_LOG_FORMAT = "--format=%h%x1f%s%x1f%an%x1f%ct%x1f%D"


def log(path, count=LOG_COUNT):
    """HEAD's last commits, each with the refs on it (branches, remote branches, tags,
    manifest-rev)."""
    return _log_rows(gitcmd.run(["log", f"-{count}", "--decorate=short", _LOG_FORMAT], path, check=False))


def upstream_info(path, upstream, behind):
    """Where the upstream is when HEAD's log does not show it (it has commits HEAD lacks):
    {ref, behind, commits: [the newest of them]}; None when HEAD's log has it."""
    if not upstream or not behind:
        return None
    text = gitcmd.run(["log", "-3", "--decorate=short", _LOG_FORMAT, "HEAD.." + upstream], path, check=False)
    return {"ref": upstream, "behind": behind, "commits": _log_rows(text)}


def nearest_tag(path):
    """The newest tag HEAD contains ("" when none)."""
    return gitcmd.run(["describe", "--tags", "--abbrev=0"], path, check=False).strip()


def remotes(path):
    out = {}
    for line in gitcmd.run(["remote", "-v"], path, check=False).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.setdefault(parts[0], parts[1])
    return out


# Checked in the repo's top when commit.template is not set (Realtek repos ship one).
TEMPLATE_FILES = (".git-commit-template", ".gitmessage", ".commit-template")


def commit_template(path):
    """-> (text, where it came from) of the commit template: commit.template, else a
    template file in the repo's top; ("", "") when none or unreadable."""
    name = gitcmd.run(["config", "--get", "commit.template"], path, check=False).strip()
    if name:
        name = os.path.expanduser(name)
        names = [name if os.path.isabs(name) else os.path.join(path, name)]
    else:
        names = [os.path.join(path, n) for n in TEMPLATE_FILES]
    for name in names:
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                return f.read(), name
        except OSError:
            continue
    return "", ""


def hook_path(path):
    """Where git looks for the commit-msg hook (core.hooksPath honoured)."""
    rel = gitcmd.run(["rev-parse", "--git-path", "hooks/commit-msg"], path).strip()
    return os.path.normpath(os.path.join(path, rel))


def detail(path):
    """Everything the drawer shows of one repository."""
    st = parse_porcelain(gitcmd.run(["status", "--porcelain=v2", "--branch"], path))
    st.in_progress = in_progress(path)
    template, template_file = commit_template(path)
    return {"status": st.as_dict(), "changes": changes(path), "log": log(path), "remotes": remotes(path),
            "upstream": upstream_info(path, st.upstream, st.behind), "tag": nearest_tag(path),
            "template": template, "template_file": template_file, "hook": os.path.isfile(hook_path(path)),
            "backups": backups(path), "stashes": stash_count(path)}


def _safe_rel(path, rel):
    """rel must name something inside path: no absolute, no .., no option-looking name."""
    norm = os.path.normpath(rel)
    if not rel or os.path.isabs(norm) or norm == ".." or norm.startswith(".." + os.sep) or rel.startswith("-"):
        raise OpError(f"不在仓库内的路径：{rel}")
    return rel


def diff(path, rel, staged=False, untracked=False):
    rel = _safe_rel(path, rel)
    full = os.path.join(path, rel)
    if untracked and os.path.isdir(full):
        if gitcmd.is_repo_dir(full):
            return f"（{rel} 是一个独立的 git 仓库，例如嵌套的 west project）"
        names = sorted(os.listdir(full))
        return f"（未跟踪的目录，{len(names)} 项）\n" + "\n".join(names[:200])
    if untracked:
        args = ["diff", "--no-index", "--", os.devnull, rel]
    else:
        args = ["diff", *(["--cached"] if staged else []), "--", rel]
    text = gitcmd.run(args, path, check=False)
    if len(text) > MAX_DIFF:
        text = text[:MAX_DIFF] + f"\n... (超过 {MAX_DIFF // 1000} KB，后面省略)"
    return text


def stage(path, files):
    files = [_safe_rel(path, f) for f in files or []]
    if files:
        gitcmd.run(["add", "--", *files], path)


def unstage(path, files):
    files = [_safe_rel(path, f) for f in files or []]
    if not files:
        return
    has_head = gitcmd.run(["rev-parse", "--verify", "-q", "HEAD"], path, check=False).strip()
    gitcmd.run(["restore", "--staged", "--", *files] if has_head else ["rm", "--cached", "-q", "--", *files], path)


def commit_risks(path, amend):
    """Reasons to ask first: amending a commit that is already on a remote branch."""
    if not amend:
        return []
    remote = gitcmd.run(["branch", "-r", "--contains", "HEAD"], path, check=False).strip()
    if remote:
        return [f"HEAD 已经在远端分支上（{remote.splitlines()[0].strip()}），amend 后需要重新推送"]
    return []


def commit(path, message, amend=False):
    """git commit (comments stripped, as with the editor) -> the new HEAD's short sha."""
    if not message.strip():
        raise OpError("commit message 是空的")
    if not amend and not gitcmd.run(["diff", "--cached", "--name-only"], path).strip():
        raise OpError("没有 staged 的改动，先勾选要提交的文件")
    fd, tmp = tempfile.mkstemp(prefix="apiary-msg-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(message)
        try:
            gitcmd.run(["commit", "--cleanup=strip", "-F", tmp, *(["--amend"] if amend else [])], path, timeout=60)
        except gitcmd.GitError as exc:
            raise OpError(f"commit 失败：{exc}") from None
    finally:
        os.unlink(tmp)
    return gitcmd.run(["rev-parse", "--short", "HEAD"], path).strip()


_SCP_URL = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?!//)")


def gerrit_ssh(url):
    """A remote url -> (user, host, port) for Gerrit's ssh, or None when it is not ssh."""
    if url.startswith("ssh://"):
        u = urlparse(url)
        return u.username, u.hostname, u.port or 22
    m = _SCP_URL.match(url)
    if m and not re.match(r"^[A-Za-z]:[\\/]", url):
        return m.group("user"), m.group("host"), 22
    return None


def install_hook(path):
    """Fetch Gerrit's commit-msg hook (adds the Change-Id) from the repo's remote."""
    urls = list(remotes(path).values())
    target = next((gerrit_ssh(u) for u in urls if gerrit_ssh(u)), None)
    if not target:
        raise OpError("remote 不是 ssh 地址，无法自动取 hook；请手动安装 commit-msg hook")
    user, host, port = target
    dest = hook_path(path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = f"{user + '@' if user else ''}{host}:hooks/commit-msg"
    manual = f"scp -O -p -P {port} {src} \"{dest}\""
    errors = []
    # -O: the scp protocol; newer OpenSSH defaults to SFTP, which Gerrit does not speak.
    for legacy in (["-O"], []):
        cmd = ["scp", *legacy, "-p", "-o", "BatchMode=yes", "-P", str(port), src, dest]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30, env=gitcmd.env(), stdin=subprocess.DEVNULL,
                                  creationflags=gitcmd.CREATE_FLAGS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            continue
        if proc.returncode == 0 and os.path.isfile(dest):
            return dest
        errors.append(proc.stderr.decode("utf-8", "replace").strip())
    raise OpError(f"安装 hook 失败：{errors[-1] if errors else ''}\n可以手动执行：{manual}")


# --- jobs ------------------------------------------------------------------------

def fetch_steps(repos):
    return [Step(r.name, ["git", "fetch", "--all", "--prune"], r.abspath) for r in repos if r.cloned]


def pull_plan(repos, statuses, confirm):
    """-> (steps, skipped [(name, why)], need_confirm [why])."""
    steps, skipped, risks = [], [], []
    for r in repos:
        st = statuses.get(r.id)
        if not r.cloned or st is None or st.error:
            skipped.append((r.name, st.error if st and st.error else "未 clone"))
        elif st.branch is None:
            skipped.append((r.name, "detached HEAD，没有可 pull 的分支"))
        elif st.upstream is None:
            skipped.append((r.name, f"分支 {st.branch} 没有 upstream"))
        else:
            argv = ["git", "pull", "--rebase"]
            if st.dirty:
                risks.append(f"{r.name}: 有未提交改动，将使用 --autostash")
                argv.append("--autostash")
            steps.append(Step(r.name, argv, r.abspath))
    return steps, skipped, ([] if confirm else risks)


def _count_args(path, revs):
    out = gitcmd.run(["rev-list", "--count", *revs], path, check=False).strip()
    return int(out) if out.isdigit() else 0


def update_check(ws, projects, statuses, keep_descendants=True, rebase=False):
    """What `west update` would do to each project (projects: names, empty = all active).
    -> {rows: [...], risks: [...]}; read only."""
    chosen = set(projects or [])
    rows, risks = [], []
    for r in ws.repos:
        if r.role != "project" or not r.declared.active or (chosen and r.name not in chosen):
            continue
        row = {"name": r.name, "path": r.path, "revision": r.declared.revision, "cloned": r.cloned, "notes": []}
        rows.append(row)
        if not r.cloned:
            row["info"] = "将 clone"
            continue
        st = statuses.get(r.id)
        if st is None or st.error:
            row["notes"].append(f"状态读取失败：{st.error if st else ''}")
            continue
        row.update(branch=st.branch, dirty=st.dirty, on_manifest=st.on_manifest)
        if st.dirty:
            msg = "有未提交改动，checkout 可能失败或带着改动切换"
            row["notes"].append(msg)
            risks.append(f"{r.name}: {msg}")
        # commits only here: on HEAD, on neither manifest-rev nor any remote branch
        local = _count_args(r.abspath, ["HEAD", "--not", "refs/heads/manifest-rev", "--remotes"]) if st.manifest_rev else 0
        if local:
            if rebase:
                msg = f"有 {local} 个本地 commit，将 rebase 到新的 manifest-rev"
            elif keep_descendants:
                msg = f"有 {local} 个本地 commit；HEAD 若是新 revision 的后代则保留，否则 detach"
            else:
                msg = f"有 {local} 个本地 commit，HEAD 将 detach 到新 revision（commit 仍在分支 {st.branch or '-'} 上）"
            row["notes"].append(msg)
            risks.append(f"{r.name}: {msg}")
        elif st.branch and not rebase:
            row["info"] = ("HEAD 是新 revision 的后代则留在分支上，否则 detached" if keep_descendants
                           else "update 后为 detached HEAD")
    return {"rows": rows, "risks": risks}


def update_steps(ws, projects, west_path="", keep_descendants=True, rebase=False, narrow=False):
    argv, _ = westcli.command(west_path)
    opts = [o for o, on in (("--keep-descendants", keep_descendants), ("--rebase", rebase),
                            ("--narrow", narrow)) if on]
    return [Step("west update", [*argv, "update", *opts, *(projects or [])], ws.path)]


# --- sync to the manifest / unstick ------------------------------------------------

SYNC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync.py")


def _rev_kind(revision):
    rev = revision.removeprefix("refs/heads/")
    if rev.startswith("refs/tags/"):
        return "tag"
    return "commit" if re.fullmatch(r"[0-9a-f]{7,40}", rev) else "branch"


def _project_repos(ws, projects):
    chosen = set(projects or [])
    return [r for r in ws.repos if r.role == "project" and r.declared.active and (not chosen or r.name in chosen)]


def stuck(st, declared=None):
    """Why a repository is stuck, or None: what blocks a sync / pull of it.
    -> {kind, text, fixes: [action]}; the actions are unstick()'s."""
    if st is None or st.error:
        return None
    if st.in_progress:
        fixes = ["abort", "continue"] if st.in_progress in ("rebase", "merge", "cherry-pick", "revert", "am") else ["abort"]
        if st.in_progress == "rebase":
            fixes.append("skip")
        return {"kind": "in_progress", "text": f"{st.in_progress} 进行中" + (f"，{st.conflicted} 个冲突" if st.conflicted else ""),
                "fixes": fixes + ["align"]}
    if st.conflicted:
        return {"kind": "conflict", "text": f"{st.conflicted} 个文件冲突未解决", "fixes": ["align"]}
    if declared is None:
        return None
    want = declared.revision.removeprefix("refs/heads/")
    if _rev_kind(declared.revision) == "branch":
        if st.branch is None:
            return {"kind": "detached", "text": f"detached，manifest 要的是分支 {want}", "fixes": ["sync", "align"]}
        if st.branch != want:
            return {"kind": "other_branch", "text": f"在分支 {st.branch} 上，manifest 要的是 {want}", "fixes": ["sync", "align"]}
        if st.upstream is None:
            return {"kind": "no_upstream", "text": f"分支 {want} 没有 upstream", "fixes": ["sync", "align"]}
        if st.ahead and st.behind:
            return {"kind": "diverged", "text": f"和 {st.upstream} 分叉（↑{st.ahead} ↓{st.behind}）", "fixes": ["sync", "align"]}
    return None


def sync_plan(ws, projects, statuses, autostash=()):
    """What a sync would do to each active project -> {rows, risks}; read only.
    autostash: names whose uncommitted changes go along (stashed around it); the
    other dirty ones are skipped."""
    rows, risks = [], []
    for r in _project_repos(ws, projects):
        d = r.declared
        kind = _rev_kind(d.revision)
        want = d.revision.removeprefix("refs/heads/")
        row = {"name": r.name, "path": r.path, "id": r.id, "revision": d.revision, "kind": kind,
               "cloned": r.cloned, "action": "", "notes": [], "dirty": False, "skip": False}
        rows.append(row)
        if not r.cloned:
            row.update(action="未 clone：先 west update", skip=True)
            continue
        st = statuses.get(r.id)
        if st is None or st.error:
            row.update(action="状态读取失败", skip=True, notes=[st.error if st else ""])
            continue
        row.update(branch=st.branch, head=st.head, dirty=st.dirty)
        s = stuck(st)
        if s:
            row.update(action="卡住：" + s["text"], skip=True, stuck=s)
            continue
        off = stuck(st, d)                    # the wrong branch: sync fixes it, say so
        if off:
            row["notes"].append(off["text"])
        if st.dirty and r.name not in autostash:
            row.update(action="有未提交改动，跳过（勾选 autostash 可带上）", skip=True)
            continue
        if kind == "branch":
            if st.branch == want:
                row["action"] = f"fetch，rebase {want} 到 {d.remote_name}/{want}"
            elif st.branch is None:
                row["action"] = f"切到分支 {want}（没有就从远端建），再 rebase"
            else:
                row["action"] = f"从 {st.branch} 切到 {want}，再 rebase"
                risks.append(f"{r.name}: 当前在分支 {st.branch}，将切到 {want}（{st.branch} 保留）")
        else:
            row["action"] = f"fetch，detach 到 {kind} {want}"
            if st.branch:
                row["notes"].append(f"离开分支 {st.branch}（分支保留）")
        if st.dirty:
            row["notes"].append("改动会 stash 后带回")
            risks.append(f"{r.name}: 有未提交改动，stash 后带回，冲突时留在 stash 里")
    return {"rows": rows, "risks": risks}


def _sync_argv(action, r, autostash=False):
    d = r.declared
    return [sys.executable, SYNC_SCRIPT, action, "--remote", d.remote_name, "--revision", d.revision,
            "--url", d.url or "", *(["--autostash"] if autostash else [])]


def sync_steps(plan, ws):
    by_name = {r.name: r for r in ws.repos}
    autostash = {row["name"] for row in plan["rows"] if row["dirty"]}
    return [Step(row["name"], _sync_argv("sync", by_name[row["name"]], row["name"] in autostash),
                 by_name[row["name"]].abspath, skip_rc=2) for row in plan["rows"] if not row["skip"]]


def align_risks(repos, statuses):
    """What a forced align throws aside for each repo (all of it recoverable)."""
    out = []
    for r in repos:
        st = statuses.get(r.id)
        if not st or st.error:
            continue
        parts = []
        if st.in_progress:
            parts.append(f"放弃 {st.in_progress}")
        if st.dirty:
            parts.append(f"{st.staged + st.modified + st.conflicted} 个改动文件 stash 起来")
        if st.ahead:
            parts.append(f"{st.ahead} 个本地 commit 移到备份 ref")
        elif st.on_manifest is False:
            parts.append("当前 HEAD 备份到 refs/apiary/backup/")
        out.append(f"{r.name}: " + ("，".join(parts) if parts else "直接对齐"))
    return out


def align_steps(repos):
    return [Step(r.name, _sync_argv("align", r), r.abspath, skip_rc=2) for r in repos
            if r.cloned and r.role == "project" and r.declared]


_UNSTICK = {
    "abort": {"rebase": ["rebase", "--abort"], "am": ["am", "--abort"], "merge": ["merge", "--abort"],
              "cherry-pick": ["cherry-pick", "--abort"], "revert": ["revert", "--abort"]},
    "continue": {"rebase": ["-c", "core.editor=true", "rebase", "--continue"],
                 "am": ["am", "--continue"], "merge": ["-c", "core.editor=true", "merge", "--continue"],
                 "cherry-pick": ["-c", "core.editor=true", "cherry-pick", "--continue"],
                 "revert": ["-c", "core.editor=true", "revert", "--continue"]},
    "skip": {"rebase": ["rebase", "--skip"], "am": ["am", "--skip"], "cherry-pick": ["cherry-pick", "--skip"]},
}


def unstick(path, action):
    """abort / continue / skip the operation half done at path -> git's output."""
    op = in_progress(path)
    if not op:
        raise OpError("这个仓库没有进行中的 rebase / merge / cherry-pick")
    argv = _UNSTICK.get(action, {}).get(op)
    if not argv:
        raise OpError(f"{op} 不支持 {action}")
    try:
        return gitcmd.run(argv, path, timeout=60)
    except gitcmd.GitError as exc:
        hint = "（还有未解决的冲突：改好后 stage 再继续）" if action == "continue" else ""
        raise OpError(f"{op} --{action} 失败{hint}：{exc}") from None


def backups(path):
    """The refs a forced align kept: [{ref, sha, subject, time}], newest first."""
    text = gitcmd.run(["for-each-ref", "--sort=-refname", "--format=%(refname)%1f%(objectname:short)%1f%(subject)%1f%(committerdate:unix)",
                       "refs/apiary/backup/"], path, check=False)
    out = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append({"ref": parts[0], "sha": parts[1], "subject": parts[2], "time": int(parts[3] or 0)})
    return out


def stash_count(path):
    return len(gitcmd.run(["stash", "list"], path, check=False).splitlines())


def restore_backup(path, ref):
    """A new branch at a backup ref -> its name (the work tree stays as it is)."""
    if not ref.startswith("refs/apiary/backup/"):
        raise OpError("只能恢复 refs/apiary/backup/ 下的备份")
    name = "apiary-restore-" + ref.rsplit("/", 1)[-1]
    try:
        gitcmd.run(["branch", name, ref], path)
    except gitcmd.GitError as exc:
        raise OpError(f"建分支失败：{exc}") from None
    return name


def drop_backup(path, ref):
    if not ref.startswith("refs/apiary/backup/"):
        raise OpError("只能删除 refs/apiary/backup/ 下的备份")
    gitcmd.run(["update-ref", "-d", ref], path)


def set_manifest_file(ws_path, manifest_repo, rel):
    """Point the workspace at another yml of its manifest repository (manifest.file in
    .west/config), as `west config manifest.file` does."""
    import configparser
    norm = os.path.normpath(rel)
    full = os.path.join(manifest_repo, norm)
    if os.path.isabs(norm) or norm.startswith(".."):
        raise OpError(f"不在 manifest 仓库里的文件：{rel}")
    if not os.path.isfile(full) or not norm.endswith((".yml", ".yaml")):
        raise OpError(f"manifest 仓库里没有 {rel}")
    cfg_path = os.path.join(ws_path, ".west", "config")
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path, encoding="utf-8")
    if not cfg.has_section("manifest"):
        cfg.add_section("manifest")
    cfg.set("manifest", "file", norm.replace(os.sep, "/"))
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
        cfg.write(f)


def forall_steps(ws, cmd, projects, west_path=""):
    if not cmd.strip():
        raise OpError("命令是空的")
    argv, _ = westcli.command(west_path)
    return [Step(f"west forall -c {cmd}", [*argv, "forall", "-c", cmd, *(projects or [])], ws.path)]


def git_steps(repos, text):
    """`git <args>` in each repo; text as typed ("git " in front is fine), split like a shell."""
    try:
        args = shlex.split(text.strip())
    except ValueError as exc:
        raise OpError(f"命令解析失败：{exc}") from None
    if args[:1] == ["git"]:
        args = args[1:]
    if not args:
        raise OpError("命令是空的")
    return [Step(r.name, ["git", *args], r.abspath) for r in repos if r.cloned]


# --- open --------------------------------------------------------------------------

def _git_root():
    """Git for Windows' install directory (where git-bash.exe is), or None."""
    exe = gitcmd.git_exe()
    d = os.path.dirname(os.path.realpath(exe)) if exe else None
    for _ in range(4):
        if not d:
            break
        if os.path.isfile(os.path.join(d, "git-bash.exe")):
            return d
        d = os.path.dirname(d) if os.path.dirname(d) != d else None
    for d in (r"C:\Program Files\Git", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git")):
        if os.path.isfile(os.path.join(d, "git-bash.exe")):
            return d
    return None


def _vscode():
    for name in ("code",):
        found = shutil.which(name)
        if found:
            exe = os.path.join(os.path.dirname(os.path.dirname(found)), "Code.exe")
            return exe if os.path.isfile(exe) else found
    for d in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code"), r"C:\Program Files\Microsoft VS Code"):
        if os.path.isfile(os.path.join(d, "Code.exe")):
            return os.path.join(d, "Code.exe")
    return None


def tools():
    """{name: path or None} of what the open menu can start."""
    root = _git_root()
    return {"gitbash": os.path.join(root, "git-bash.exe") if root else None,
            "gitk": os.path.join(root, "cmd", "gitk.exe") if root and os.path.isfile(os.path.join(root, "cmd", "gitk.exe")) else None,
            "vscode": _vscode(), "wt": _wt(), "claude": shutil.which("claude")}


def _wt():
    """Windows Terminal: an app execution alias, which shutil.which does not see."""
    alias = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe")
    return shutil.which("wt") or (alias if os.path.exists(alias) else None)


def open_with(what, path, terminal="gitbash", claude_cmd="claude"):
    """Start a program on path, detached from the UI. what: terminal | gitbash | cmd | wt |
    claude | vscode | explorer | gitk | gitgui | file."""
    if what == "terminal":
        what = terminal
    t = tools()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    cwd = path if os.path.isdir(path) else os.path.dirname(path)
    if what == "file" or what == "explorer":
        os.startfile(path)            # noqa: S606 - the user's own file, the system's handler
        return
    if what == "cmd":
        subprocess.Popen(["cmd.exe", "/K"], cwd=cwd, creationflags=new_console)
        return
    if what == "claude":
        # The command as typed in cmd (settings: claude_cmd); /K keeps the window when it exits.
        cmd = (claude_cmd or "claude").strip()
        if cmd.split()[0].lower() in ("claude", "claude.exe") and not t["claude"]:
            raise OpError("找不到 claude（Claude Code CLI）：请确认它在 PATH 里，或在 Settings 里改启动命令")
        subprocess.Popen(f'cmd.exe /K "title Claude Code - {os.path.basename(cwd) or cwd} & {cmd}"',
                         cwd=cwd, creationflags=new_console)
        return
    if what == "gitgui":
        subprocess.Popen(["git", "gui"], cwd=cwd, creationflags=flags)
        return
    argv = {"gitbash": lambda p: [p, f"--cd={cwd}"], "wt": lambda p: [p, "-d", cwd],
            "vscode": lambda p: [p, path], "gitk": lambda p: [p, "--all"]}.get(what)
    if argv is None:
        raise OpError(f"不认识的打开方式：{what}")
    if not t.get(what):
        raise OpError({"gitbash": "找不到 git-bash.exe", "wt": "找不到 Windows Terminal (wt.exe)",
                       "vscode": "找不到 VS Code", "gitk": "找不到 gitk"}[what])
    subprocess.Popen(argv(t[what]), cwd=cwd, creationflags=flags, close_fds=True)


def start_detached(argv, cwd):
    """A program the UI does not wait for (VS Code at a line, Explorer at a file)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(argv, cwd=cwd, creationflags=flags, close_fds=True)


def open_files(ws_path, depth=2):
    """Files worth opening from a workspace's menu: Keil multi-projects, VS Code workspaces."""
    out = []
    for base, dirs, names in os.walk(ws_path):
        rel = os.path.relpath(base, ws_path)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [d for d in dirs if not d.startswith(".") and level < depth]
        out += [os.path.join(base, n) for n in names if n.endswith((".uvmpw", ".code-workspace"))]
    return sorted(out, key=str.lower)
