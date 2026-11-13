"""The UI's API: plain calls returning JSON-ready dicts, no HTTP here (server.py).

The workspaces are scanned once (and again on rescan / a settings change) and kept;
statuses are read fresh on every call that needs them. A write that could lose work
asks first: called without confirm it answers {"need_confirm": [why, ...]} and does
nothing; the page asks the user and calls again with confirm=True.
"""
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import build, claude_history, config, discover, gitcmd, gitstat, ops, westcli
from ..jobs import Busy, Jobs, Step

BUILD_SCRIPT = build.__file__


class ApiError(Exception):
    """A request the API refuses: the message is what the page shows. status: HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _repo_dict(r):
    d = {"id": r.id, "name": r.name, "path": r.path, "abspath": r.abspath, "role": r.role, "cloned": r.cloned}
    if r.declared:
        d["declared"] = {"revision": r.declared.revision, "url": r.declared.url,
                         "groups": r.declared.groups, "active": r.declared.active}
    return d


def _ws_dict(ws):
    m = ws.manifest
    return {"id": ws.id, "name": ws.name, "path": ws.path, "kind": ws.kind, "error": ws.error,
            "manifest": {"file": m.file, "repo": m.repo, "group_filter": m.group_filter} if m else None,
            "repos": [_repo_dict(r) for r in ws.repos]}


def _declared(r):
    """What the manifest wants of r (stuck() checks the branch), None when nothing."""
    return r.declared if r.role == "project" and r.declared and r.declared.active else None


def summary(ws, statuses):
    """The card's badges: counts over ws's repos."""
    s = {"repos": len(ws.repos), "cloned": 0, "dirty": 0, "untracked": 0, "ahead": 0, "behind": 0,
         "off_manifest": 0, "detached": 0, "errors": 0, "not_cloned": 0, "stuck": 0, "fetched_at": None}
    for r in ws.repos:
        st = statuses.get(r.id)
        if not r.cloned:
            s["not_cloned"] += bool(r.declared and r.declared.active)
            continue
        s["cloned"] += 1
        if st is None:
            continue
        if st.error:
            s["errors"] += 1
            continue
        s["dirty"] += st.dirty
        s["untracked"] += bool(st.untracked)
        s["ahead"] += bool(st.ahead)
        s["behind"] += bool(st.behind)
        s["off_manifest"] += st.on_manifest is False
        s["detached"] += st.branch is None and r.role != "project"
        s["stuck"] += ops.stuck(st, _declared(r)) is not None
        if st.fetched_at and (s["fetched_at"] is None or st.fetched_at < s["fetched_at"]):
            s["fetched_at"] = st.fetched_at          # the oldest: how stale it can be
    s["attention"] = s["dirty"] + s["off_manifest"] + s["errors"] + s["behind"] + s["not_cloned"] + s["stuck"]
    return s


class Api:
    def __init__(self, settings=None, save=True):
        self.settings = settings or config.load()
        self._save = save
        self.jobs = Jobs()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="apiary")
        self._workspaces = None
        self.scanned_at = None
        self._targets = {}          # (ws id, IC) -> build.scan()

    # --- workspaces ---
    def _scan(self):
        found = discover.scan(self.settings, self._pool)
        with self._lock:
            self._workspaces = {ws.id: ws for ws in found}
            self.scanned_at = time.time()

    def _all(self):
        if self._workspaces is None:
            self._scan()
        return list(self._workspaces.values())

    def _ws(self, ws_id):
        self._all()
        ws = self._workspaces.get(discover.norm_id(ws_id)) or self._workspaces.get(ws_id)
        if not ws:
            raise ApiError("没有这个 workspace（可能已被删除或隐藏，请刷新）", 404)
        return ws

    def _repos(self, ws, repo_ids):
        """ws's repos by id (empty: all of them)."""
        if not repo_ids:
            return list(ws.repos)
        wanted = {discover.norm_id(i) for i in repo_ids}
        out = [r for r in ws.repos if r.id in wanted]
        if len(out) != len(wanted):
            raise ApiError("有的仓库不在这个 workspace 里，请刷新", 404)
        return out

    def _repo(self, ws_id, repo_id):
        ws = self._ws(ws_id)
        r = ws.repo(discover.norm_id(repo_id))
        if not r:
            raise ApiError("这个 workspace 里没有这个仓库，请刷新", 404)
        if not r.cloned:
            raise ApiError(f"{r.name} 还没有 clone")
        return ws, r

    def workspaces(self):
        """Every workspace with its repos (no status: that is status())."""
        wss = self._all()
        return {"workspaces": [_ws_dict(ws) | {"busy": self.jobs.busy(ws.id)} for ws in wss],
                "scanned_at": self.scanned_at}

    def rescan(self):
        self._scan()
        return self.workspaces()

    def status(self, ws_ids=None):
        """{ws_id: {summary, repos: {repo_id: status}}} for ws_ids (None: all), read now."""
        wss = [self._ws(i) for i in ws_ids] if ws_ids else self._all()
        repos = [r for ws in wss for r in ws.repos]
        sts = gitstat.statuses(repos)
        return {"status": {ws.id: {"summary": summary(ws, sts), "busy": self.jobs.busy(ws.id),
                                   "repos": {r.id: sts[r.id].as_dict() | {"stuck": ops.stuck(
                                       sts[r.id], _declared(r))}
                                             for r in ws.repos if r.id in sts}}
                           for ws in wss},
                "at": time.time()}

    # --- the repo drawer ---
    def repo(self, ws_id, repo_id):
        ws, r = self._repo(ws_id, repo_id)
        try:
            d = ops.detail(r.abspath)
        except gitcmd.GitError as exc:
            raise ApiError(str(exc)) from None
        st = gitstat.RepoStatus(**{k: v for k, v in d["status"].items() if k != "dirty"})
        return d | {"repo": _repo_dict(r), "ws_id": ws.id,
                    "stuck": ops.stuck(st, _declared(r))}

    def diff(self, ws_id, repo_id, path, staged=False, untracked=False):
        _, r = self._repo(ws_id, repo_id)
        try:
            return {"diff": ops.diff(r.abspath, path, staged=_flag(staged), untracked=_flag(untracked))}
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None

    def stage(self, ws_id, repo_id, files, unstage=False):
        _, r = self._repo(ws_id, repo_id)
        try:
            (ops.unstage if unstage else ops.stage)(r.abspath, files)
        except (ops.OpError, gitcmd.GitError) as exc:
            raise ApiError(str(exc)) from None
        return {"changes": ops.changes(r.abspath)}

    def commit(self, ws_id, repo_id, message, amend=False, confirm=False):
        _, r = self._repo(ws_id, repo_id)
        risks = ops.commit_risks(r.abspath, amend)
        if risks and not confirm:
            return {"need_confirm": risks}
        try:
            sha = ops.commit(r.abspath, message, amend=amend)
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None
        return {"sha": sha}

    def install_hook(self, ws_id, repo_id):
        _, r = self._repo(ws_id, repo_id)
        try:
            return {"hook": ops.install_hook(r.abspath)}
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None

    # --- manifest ---
    def manifest(self, ws_id):
        ws = self._ws(ws_id)
        if ws.kind != "west" or not ws.manifest:
            raise ApiError("这不是 west workspace，或 manifest 读取失败")
        m = ws.manifest
        return {"file": m.file, "repo": m.repo, "group_filter": m.group_filter,
                "files": m.files(), "current": os.path.relpath(m.file, m.repo),
                "projects": [_repo_dict(r) for r in ws.repos if r.role in ("project", "manifest")]}

    def manifest_raw(self, ws_id, file=None):
        ws = self._ws(ws_id)
        if not ws.manifest:
            raise ApiError("这不是 west workspace")
        m = ws.manifest
        rel = file or os.path.relpath(m.file, m.repo)
        if rel not in m.files():
            raise ApiError(f"manifest 仓库里没有 {rel}", 404)
        with open(os.path.join(m.repo, rel), encoding="utf-8", errors="replace") as f:
            return {"file": rel, "text": f.read()}

    # --- jobs ---
    def _start(self, title, ws, steps, keep_going=True):
        if not steps:
            raise ApiError("没有可以执行的仓库")
        try:
            job = self.jobs.start(title, ws.id, steps, keep_going)
        except Busy as exc:
            raise ApiError(str(exc), 409) from None
        return {"job": job.as_dict()}

    def fetch(self, ws_id, repo_ids=None):
        ws = self._ws(ws_id)
        repos = self._repos(ws, repo_ids)
        return self._start(f"fetch · {ws.name}" + (f" · {repos[0].name}" if len(repos) == 1 else ""),
                           ws, ops.fetch_steps(repos))

    def fetch_all(self):
        """A fetch Job for each workspace that has none going."""
        started, skipped = [], []
        for ws in self._all():
            if self.jobs.busy(ws.id):
                skipped.append(ws.name)
                continue
            steps = ops.fetch_steps(ws.repos)
            if steps:
                started.append(self.jobs.start(f"fetch · {ws.name}", ws.id, steps).as_dict())
        return {"jobs": started, "skipped": skipped}

    def pull(self, ws_id, repo_ids=None, confirm=False):
        ws = self._ws(ws_id)
        repos = self._repos(ws, repo_ids)
        steps, skipped, risks = ops.pull_plan(repos, gitstat.statuses(repos), _flag(confirm))
        if risks:
            return {"need_confirm": risks, "skipped": skipped}
        if not steps:
            raise ApiError("没有可以 pull 的仓库：" + "；".join(f"{n}: {why}" for n, why in skipped))
        out = self._start(f"pull --rebase · {ws.name}" + (f" · {repos[0].name}" if len(repos) == 1 else ""),
                          ws, steps)
        return out | {"skipped": skipped}

    def _west_ws(self, ws_id):
        ws = self._ws(ws_id)
        if ws.kind != "west" or not ws.manifest:
            raise ApiError("这不是 west workspace，或 manifest 读取失败")
        return ws

    def update_check(self, ws_id, projects=None, keep_descendants=True, rebase=False):
        ws = self._west_ws(ws_id)
        sts = gitstat.statuses([r for r in ws.repos if r.role == "project"])
        return ops.update_check(ws, projects, sts, _flag(keep_descendants), _flag(rebase))

    def update(self, ws_id, projects=None, keep_descendants=True, rebase=False, narrow=False, confirm=False):
        ws = self._west_ws(ws_id)
        check = self.update_check(ws_id, projects, keep_descendants, rebase)
        if check["risks"] and not _flag(confirm):
            return {"need_confirm": check["risks"]}
        steps = ops.update_steps(ws, projects, self.settings.west_path, _flag(keep_descendants), _flag(rebase),
                                 _flag(narrow))
        return self._start(f"west update · {ws.name}" + (f" · {len(projects)} 个 project" if projects else ""),
                           ws, steps)

    # --- sync to the manifest / stuck repos ---
    def _project_statuses(self, ws):
        return gitstat.statuses([r for r in ws.repos if r.role == "project"])

    def sync_check(self, ws_id, projects=None, autostash=None):
        ws = self._west_ws(ws_id)
        return ops.sync_plan(ws, projects, self._project_statuses(ws), set(autostash or []))

    def sync(self, ws_id, projects=None, autostash=None, confirm=False):
        ws = self._west_ws(ws_id)
        plan = self.sync_check(ws_id, projects, autostash)
        if plan["risks"] and not _flag(confirm):
            return {"need_confirm": plan["risks"]}
        steps = ops.sync_steps(plan, ws)
        if not steps:
            raise ApiError("没有可以同步的 project：" + "；".join(f"{r['name']}: {r['action']}" for r in plan["rows"]))
        out = self._start(f"同步到 manifest · {ws.name}" + (f" · {len(projects)} 个 project" if projects else ""), ws, steps)
        return out | {"skipped": [(r["name"], r["action"]) for r in plan["rows"] if r["skip"]]}

    def stuck(self, ws_id):
        """{repo_id: why} of ws's stuck repos (see ops.stuck), read now."""
        ws = self._ws(ws_id)
        sts = gitstat.statuses(ws.repos)
        out = {}
        for r in ws.repos:
            s = ops.stuck(sts.get(r.id), _declared(r))
            if s:
                out[r.id] = s
        return {"stuck": out}

    def align(self, ws_id, repo_ids, confirm=False):
        ws = self._west_ws(ws_id)
        repos = [r for r in self._repos(ws, repo_ids) if r.role == "project" and r.cloned]
        if not repos:
            raise ApiError("强制对齐只对 manifest 里已 clone 的 project 有效")
        if not _flag(confirm):
            return {"need_confirm": ops.align_risks(repos, self._project_statuses(ws))}
        return self._start(f"强制对齐 · {ws.name}" + (f" · {repos[0].name}" if len(repos) == 1 else f" · {len(repos)} 个"),
                           ws, ops.align_steps(repos))

    def unstick(self, ws_id, repo_id, action):
        ws, r = self._repo(ws_id, repo_id)
        if self.jobs.busy(ws.id):
            raise ApiError("这个 workspace 正在执行任务，等它结束", 409)
        try:
            out = ops.unstick(r.abspath, action)
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None
        return {"output": out}

    def restore_backup(self, ws_id, repo_id, ref, drop=False):
        _, r = self._repo(ws_id, repo_id)
        try:
            if _flag(drop):
                ops.drop_backup(r.abspath, ref)
                return {"dropped": ref}
            return {"branch": ops.restore_backup(r.abspath, ref)}
        except (ops.OpError, gitcmd.GitError) as exc:
            raise ApiError(str(exc)) from None

    def set_manifest(self, ws_id, file):
        ws = self._west_ws(ws_id)
        if self.jobs.busy(ws.id):
            raise ApiError("这个 workspace 正在执行任务，等它结束", 409)
        try:
            ops.set_manifest_file(ws.path, ws.manifest.repo, file)
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None
        new = discover.probe(ws.path)
        with self._lock:
            self._workspaces[ws.id] = new
        if new.error:
            raise ApiError(f".west/config 已改为 {file}，但 manifest 读取失败：{new.error}")
        return {"workspace": _ws_dict(new)}

    def forall(self, ws_id, cmd, projects=None):
        ws = self._west_ws(ws_id)
        try:
            steps = ops.forall_steps(ws, cmd, projects, self.settings.west_path)
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None
        self._remember(f"forall: {cmd}")
        return self._start(f"west forall · {ws.name}", ws, steps)

    def git(self, ws_id, cmd, repo_ids=None):
        ws = self._ws(ws_id)
        try:
            steps = ops.git_steps(self._repos(ws, repo_ids), cmd)
        except ops.OpError as exc:
            raise ApiError(str(exc)) from None
        self._remember(f"git: {cmd.strip().removeprefix('git ').strip()}")
        return self._start(f"git {cmd.strip().removeprefix('git ').strip()} · {ws.name}", ws, steps)

    def _remember(self, cmd):
        self.settings.remember_cmd(cmd)
        if self._save:
            config.save(self.settings)

    def jobs_list(self):
        return {"jobs": self.jobs.list()}

    def cancel(self, job_id):
        return {"cancelled": self.jobs.cancel(int(job_id))}

    def events_after(self, index, timeout=10.0):
        return self.jobs.events_after(index, timeout)

    # --- build (gcc defconfig projects, build.py) ---
    def _build_ic(self, ws):
        if ws.path in self.settings.build_ic:
            return build.canon_ic(self.settings.build_ic[ws.path])
        return build.default_ic(ws.name)

    def _build_targets(self, ws, rescan=False):
        ic = self._build_ic(ws)
        key = (ws.id, ic)
        with self._lock:
            hit = self._targets.get(key)
        if hit is None or rescan:
            hit = build.scan(ws.path, ic) if ic else []
            with self._lock:
                self._targets[key] = hit
        return ic, hit

    def _picked(self, ws, ids):
        _, targets = self._build_targets(ws)
        by_id = {t["id"]: t for t in targets}
        picked = [by_id[i] for i in ids or [] if i in by_id]
        if not picked or len(picked) != len(set(ids)):
            raise ApiError("有的 target 不在列表里，请重新扫描", 404)
        return picked

    def build_targets(self, ws_id, rescan=False):
        ws = self._ws(ws_id)
        ic, targets = self._build_targets(ws, _flag(rescan))
        favs = set(self.settings.build_fav)
        out = []
        for t in targets:
            last = build.last_result(t["build_dir"])
            if last:
                last = {k: last.get(k) for k in ("action", "state", "started", "ended", "rc", "stage", "errors",
                                                  "warnings", "main_bin")}
            out.append(t | {"fav": f"{ws.path}|{t['id']}" in favs, "last": last})
        return {"ic": ic, "default_ic": build.default_ic(ws.name), "ics": list(build.ICS),
                "targets": out, "tools": build.tools(self.settings.gcc_dir)}

    def build_result(self, ws_id, target):
        t = self._picked(self._ws(ws_id), [target])[0]
        return {"target": t, "result": build.last_result(t["build_dir"])}

    def build(self, ws_id, targets, action="build", reconfigure=False):
        ws = self._ws(ws_id)
        picked = self._picked(ws, targets)
        if action not in ("build", "flash"):
            raise ApiError(f"不认识的操作：{action}")
        if action == "flash":
            if len(picked) != 1:
                raise ApiError("一次只烧录一个 target")
            if not picked[0]["flash"]:
                raise ApiError("这个 target 不能用 J-Link 烧录（只有 RTL87x2L 的工程可以）")
        if not build.find_cmake():
            raise ApiError("找不到 cmake：请安装 CMake 并加入 PATH")
        gcc = build.find_gcc(self.settings.gcc_dir)
        if not gcc and any(t["kind"] == "realtek" for t in picked):
            raise ApiError("找不到 arm-none-eabi-gcc：请在 Settings 里填 gcc 的 bin 目录")
        steps = [Step(f"{t['app']} · {t['variant']}",
                      [sys.executable, BUILD_SCRIPT, action, "--kind", t["kind"], "--src", t["src"],
                       "--defconfig", t["path"], "--build-dir", t["build_dir"], "--gcc", gcc,
                       *(["--reconfigure"] if _flag(reconfigure) else [])], t["src"]) for t in picked]
        what = "编译 + 烧录" if action == "flash" else "重新 configure + 编译" if _flag(reconfigure) else "编译"
        title = f"{what} · {ws.name} · " + (steps[0].label if len(steps) == 1 else f"{len(steps)} 个 target")
        try:
            job = self.jobs.start(title, ws.id, steps, keep_going=True, locks=("jlink",) if action == "flash" else ())
        except Busy as exc:
            raise ApiError(str(exc), 409) from None
        return {"job": job.as_dict()}

    def build_clean(self, ws_id, targets, confirm=False):
        ws = self._ws(ws_id)
        picked = [t for t in self._picked(ws, targets) if os.path.isdir(t["build_dir"])]
        if not picked:
            return {"removed": []}
        if not _flag(confirm):
            return {"need_confirm": [f"删除 {t['build_dir']}" for t in picked]}
        if self.jobs.busy(ws.id):
            raise ApiError("这个 workspace 正在执行任务，等它结束", 409)
        removed = []
        for t in picked:
            if os.path.normcase(os.path.join(ws.path, "build-cmake", "apiary")) not in os.path.normcase(t["build_dir"]):
                continue                                   # only our own build directories
            shutil.rmtree(t["build_dir"], ignore_errors=True)
            removed.append(t["build_dir"])
        return {"removed": removed}

    def set_build_ic(self, ws_id, ic):
        ws = self._ws(ws_id)
        if ic and not build.canon_ic(ic):
            raise ApiError(f"不认识的 IC：{ic}")
        self.settings.build_ic = dict(self.settings.build_ic) | {ws.path: build.canon_ic(ic) or ""}
        if self._save:
            config.save(self.settings)
        return self.build_targets(ws_id)

    def build_fav(self, ws_id, target, on=True):
        ws = self._ws(ws_id)
        key = f"{ws.path}|{target}"
        favs = [f for f in self.settings.build_fav if f != key]
        self.settings.build_fav = favs + ([key] if _flag(on) else [])
        if self._save:
            config.save(self.settings)
        return {"fav": _flag(on)}

    def _inside(self, ws, path):
        p = os.path.normcase(os.path.abspath(path or ""))
        return p.startswith(os.path.normcase(os.path.abspath(ws.path)) + os.sep)

    def goto(self, ws_id, file, line=None):
        """file:line in VS Code (a diagnostic's place); only a file inside the workspace."""
        ws = self._ws(ws_id)
        if not self._inside(ws, file) or not os.path.isfile(file):
            raise ApiError("只能打开 workspace 里的文件")
        code = ops.tools()["vscode"]
        if not code:
            raise ApiError("找不到 VS Code")
        where = f"{file}:{int(line)}" if line else file
        try:
            ops.start_detached([code, "-g", where], ws.path)
        except OSError as exc:
            raise ApiError(str(exc)) from None
        return {"opened": where}

    def reveal(self, ws_id, path):
        """A build product in Explorer (selected), or a directory; inside the workspace only."""
        ws = self._ws(ws_id)
        if not self._inside(ws, path) or not os.path.exists(path):
            raise ApiError("只能打开 workspace 里的路径")
        try:
            if os.path.isfile(path):
                ops.start_detached(["explorer.exe", "/select,", path], ws.path)
            else:
                os.startfile(path)                        # noqa: S606 - a directory of the workspace
        except OSError as exc:
            raise ApiError(str(exc)) from None
        return {"opened": path}

    # --- Claude Code history ---
    def _ws_of(self, cwd):
        """The workspace a directory is in (the deepest), or None."""
        if not cwd:
            return None
        p = os.path.normcase(os.path.abspath(cwd))
        hits = [ws for ws in self._all()
                if p == os.path.normcase(ws.path) or p.startswith(os.path.normcase(ws.path) + os.sep)]
        return max(hits, key=lambda ws: len(ws.path)) if hits else None

    def claude_sessions(self):
        out = []
        running = claude_history.live()
        for s in claude_history.sessions():
            ws = self._ws_of(s["cwd"])
            out.append(s | {"ws": ws.name if ws else None, "ws_id": ws.id if ws else None,
                            "cwd_exists": bool(s["cwd"]) and os.path.isdir(s["cwd"]),
                            "live": running.get(s["id"]),
                            "resume_cmd": claude_history.resume_command(self.settings.claude_cmd, s["id"], s["model"])})
        return {"sessions": out, "home": claude_history.home(), "busy_s": claude_history.BUSY_S}

    def claude_preview(self, session_id):
        try:
            return claude_history.preview(session_id)
        except claude_history.HistoryError as exc:
            raise ApiError(str(exc), 404) from None

    def claude_resume(self, session_id):
        try:
            s = claude_history.summarize(claude_history.find(session_id))
        except claude_history.HistoryError as exc:
            raise ApiError(str(exc), 404) from None
        if not s["cwd"] or not os.path.isdir(s["cwd"]):
            raise ApiError(f"会话的目录不在了：{s['cwd'] or '（未记录）'}")
        cmd = claude_history.resume_command(self.settings.claude_cmd, session_id, s["model"])
        try:
            ops.open_with("claude", s["cwd"], claude_cmd=cmd)
        except (ops.OpError, OSError) as exc:
            raise ApiError(str(exc)) from None
        return {"opened": s["cwd"], "cmd": cmd}

    def claude_delete(self, session_ids, confirm=False):
        ids = [i for i in session_ids or [] if i]
        if not ids:
            raise ApiError("没有选会话")
        if not _flag(confirm):
            return {"need_confirm": [f"把 {len(ids)} 个会话移到回收站（{claude_history.TRASH}，可恢复）"]}
        try:
            return claude_history.delete(ids)
        except OSError as exc:
            raise ApiError(f"移动失败：{exc}") from None

    def claude_trash(self):
        return {"batches": claude_history.trash(), "path": os.path.join(claude_history.home(), claude_history.TRASH)}

    def claude_restore(self, batch):
        try:
            return claude_history.restore(batch)
        except claude_history.HistoryError as exc:
            raise ApiError(str(exc)) from None

    def claude_empty_trash(self, batch=None, confirm=False):
        if not _flag(confirm):
            return {"need_confirm": ["永久删除" + (f"回收站里的 {batch}" if batch else "回收站里的全部会话") + "，不能恢复"]}
        try:
            return claude_history.empty_trash(batch or None)
        except claude_history.HistoryError as exc:
            raise ApiError(str(exc)) from None

    def claude_open_trash(self):
        path = os.path.join(claude_history.home(), claude_history.TRASH)
        os.makedirs(path, exist_ok=True)
        os.startfile(path)                                # noqa: S606 - our own trash directory
        return {"opened": path}

    # --- open ---
    def open(self, what, ws_id, repo_id=None, file=None):
        ws = self._ws(ws_id)
        if file:
            known = {discover.norm_id(f) for f in ops.open_files(ws.path)}
            if discover.norm_id(file) not in known:
                raise ApiError("只能打开 workspace 里列出的文件")
            path = file
        elif repo_id:
            path = self._repo(ws_id, repo_id)[1].abspath
        else:
            path = ws.path
        try:
            ops.open_with("file" if file else what, path, self.settings.terminal, self.settings.claude_cmd)
        except (ops.OpError, OSError) as exc:
            raise ApiError(str(exc)) from None
        return {"opened": path}

    def open_files(self, ws_id):
        return {"files": ops.open_files(self._ws(ws_id).path)}

    # --- settings ---
    def get_settings(self):
        from dataclasses import asdict
        return {"settings": asdict(self.settings), "path": str(config.settings_path())}

    def save_settings(self, data):
        old = (list(self.settings.roots), list(self.settings.added), list(self.settings.hidden))
        config.update(self.settings, data)
        if self._save:
            config.save(self.settings)
        if old != (self.settings.roots, self.settings.added, self.settings.hidden):
            self._workspaces = None
        return self.get_settings()

    def hide(self, ws_id):
        ws = self._ws(ws_id)
        return self.save_settings({"hidden": self.settings.hidden + [ws.path]})

    def env(self):
        """git / west / the openers found, for the banner and the Settings page."""
        git = gitcmd.git_exe()
        version = ""
        if git:
            try:
                version = gitcmd.run(["--version"], os.getcwd()).strip()
            except gitcmd.GitError as exc:
                version = str(exc)
        return {"git": git, "git_version": version, "west": westcli.describe(self.settings.west_path),
                "tools": ops.tools()}

    def close(self):
        for job in self.jobs.list():
            if job["state"] in ("queued", "running"):
                self.jobs.cancel(job["id"])
        self._pool.shutdown(wait=False)


def _flag(value):
    """true / "1" / "true" from JSON or a query string."""
    return value is True or str(value).lower() in ("1", "true", "yes", "on")
