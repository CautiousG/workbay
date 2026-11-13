"""Claude Code's past sessions, from every directory it was started in: list, preview,
resume, and delete to a trash that can be put back.

Claude Code keeps a session as  <home>/projects/<dir>/<session id>.jsonl  (one JSON
record a line; <dir> is the start directory, mangled), and next to it, by the same id:
<home>/projects/<dir>/<id>/ (subagents, tool results), <home>/file-history/<id>/,
<home>/session-env/<id>/. <home>/projects/<dir>/memory/ and <home>/history.jsonl are not
a session's: never touched. The records are Claude Code's own format: read loosely, a
field that is not there is left empty.

<home> is $CLAUDE_CONFIG_DIR, else ~/.claude.
"""
import calendar
import json
import os
import re
import shutil
import time

HEAD = TAIL = 64 * 1024
FIRST_MAX = 4 * 1024 * 1024       # how far to look for the first prompt
MODEL_TAIL = 2 * 1024 * 1024      # how far back to look for Claude's last reply
BUSY_S = 120                      # changed this recently: maybe in use, not deleted
MAX_PREVIEW = 300
MAX_TEXT = 2000
TRASH = "workbay-trash"
OLD_TRASH = "apiary-trash"                   # this tool's trash when it was called Apiary
_SID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# where else a session keeps things, by its id (relative to home)
_BESIDE = ("file-history", "session-env")

_cache = {}                       # path -> ((mtime, size), summary)


class HistoryError(Exception):
    """A refused request; the message is for the page."""


def home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def _records(data):
    """Bytes of whole and partial lines -> the JSON objects of the whole ones."""
    for line in data.split(b"\n"):
        line = line.strip()
        if line.startswith(b"{") and line.endswith(b"}"):
            try:
                yield json.loads(line)
            except ValueError:
                pass


def _text(content):
    """A message's content -> its text (tool results / images: "")."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _is_prompt(o):
    """A user record that is something the user typed (not a tool result, a command's
    echo, a reminder, a notification)."""
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain") or o.get("isCompactSummary"):
        return False
    text = _text((o.get("message") or {}).get("content")).strip()
    return bool(text) and not text.startswith("<")


def _command(o):
    """"/compact" etc. when the user record is a slash command's echo."""
    text = _text((o.get("message") or {}).get("content"))
    m = re.search(r"<command-name>(/[^<]+)</command-name>", text)
    return m[1] if m else None


def _ts(value):
    """"2026-11-11T01:47:11.526Z" -> epoch seconds, None when not one."""
    if not isinstance(value, str):
        return None
    try:
        return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def summarize(path):
    """One session file -> {id, dir, path, cwd, title, first, last_prompt, started, ended,
    size, mtime}, from its first and last 64 KB only (some are tens of MB)."""
    st = os.stat(path)
    key = (st.st_mtime, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    s = {"id": os.path.basename(path)[:-len(".jsonl")], "dir": os.path.basename(os.path.dirname(path)), "path": path,
         "cwd": None, "title": None, "first": None, "last_prompt": None, "started": None, "ended": None,
         "branch": None, "size": st.st_size, "mtime": st.st_mtime}
    with open(path, "rb") as f:
        # from the start up to the first prompt (hook output before it can be large), at least HEAD
        for line in f:
            for o in _records(line):
                s["cwd"] = s["cwd"] or o.get("cwd")
                s["branch"] = s["branch"] or o.get("gitBranch")
                s["started"] = s["started"] or _ts(o.get("timestamp"))
                if not s["first"] and _is_prompt(o):
                    s["first"] = _text(o["message"]["content"]).strip()[:300]
                _late(s, o)
            if (s["first"] and f.tell() >= HEAD) or f.tell() >= FIRST_MAX:
                break
        read = f.tell()
        tail = b""
        if st.st_size > read:
            f.seek(max(read, st.st_size - TAIL))
            tail = f.read()
        for o in _records(tail):
            s["cwd"] = s["cwd"] or o.get("cwd")
            _late(s, o)
        if not s.get("model") and st.st_size > read + TAIL:
            # the last 64 KB were all tool output: look further back for Claude's last reply
            f.seek(max(read, st.st_size - MODEL_TAIL))
            for o in _records(f.read(MODEL_TAIL - TAIL)):
                _usage(s, o)                       # in order: the last one stays
    s["ended"] = s["ended"] or s["started"] or st.st_mtime
    s.setdefault("model", None)
    s.setdefault("local", False)
    s.setdefault("context", None)
    _cache[path] = (key, s)
    return s


def _late(s, o):
    """What the newest record says wins: the title, the last prompt, the end time."""
    t = o.get("type")
    if t == "ai-title" and o.get("aiTitle"):
        s["title"] = o["aiTitle"]
    elif t in ("custom-title", "summary") and (o.get("customTitle") or o.get("summary")):
        s["title"] = o.get("customTitle") or o.get("summary")
    elif t == "last-prompt" and o.get("lastPrompt"):
        s["last_prompt"] = o["lastPrompt"][:300]
    elif t == "assistant":
        _usage(s, o)
    ts = _ts(o.get("timestamp"))
    if ts:
        s["ended"] = ts


def is_local(model):
    """expert[1m] runs on the local server (free); the rest are cloud models."""
    return bool(model) and model.lower().startswith("expert")


def window(model):
    """The context window of a model id: "...[1m]" is 1M tokens, the rest 200K."""
    return 1_000_000 if re.search(r"\[1m\]$", model or "", re.I) else 200_000


def _usage(s, o):
    """Claude's reply: the model it ran on and how full the context was then."""
    if o.get("isSidechain") or o.get("isApiErrorMessage"):
        return
    m = o.get("message") or {}
    model = o.get("requestedModel") or m.get("model")
    u = m.get("usage")
    if not model or model == "<synthetic>" or not isinstance(u, dict):
        return
    used = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if not used:
        return
    size = window(model)
    if used > size:                        # older records lack requestedModel ("...[1m]"): it was 1M
        size = 1_000_000
        model += "[1m]"                    # and resuming it on 200K would not fit
    s["model"] = model
    s["local"] = is_local(model)
    s["context"] = {"used": used, "window": size, "pct": round(100 * used / size, 1)}


def sessions():
    """Every session of every directory, the newest first."""
    base = os.path.join(home(), "projects")
    out = []
    try:
        dirs = os.listdir(base)
    except OSError:
        return []
    for d in dirs:
        full = os.path.join(base, d)
        try:
            names = os.listdir(full)
        except OSError:
            continue
        for n in names:
            if n.endswith(".jsonl") and _SID.match(n[:-len(".jsonl")]):
                try:
                    out.append(summarize(os.path.join(full, n)))
                except OSError:
                    continue
    out.sort(key=lambda s: -(s["ended"] or 0))
    return out


def _proc_start(pid):
    """A process's creation time as a FILETIME (what Claude Code records), None when gone."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return -1                      # alive; no start time to compare
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return None
        if not k32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k32.CloseHandle(h)


def live():
    """{session id: {pid, status, name}} of the Claude Code processes running now, from
    <home>/sessions/<pid>.json (each one writes its own) checked against the process
    table: a file left by a crash, or a pid now used by another program, is not live."""
    out = {}
    base = os.path.join(home(), "sessions")
    try:
        names = os.listdir(base)
    except OSError:
        return out
    for n in names:
        if not re.match(r"^\d+\.json$", n):
            continue
        try:
            with open(os.path.join(base, n), encoding="utf-8") as f:
                d = json.load(f)
            pid = int(d.get("pid") or n[:-5])
        except (OSError, ValueError, TypeError):
            continue
        started = _proc_start(pid)
        if started is None:
            continue
        want = str(d.get("procStart") or "")
        if started != -1 and want.isdigit() and abs(int(want) - started) > 10_000_000:   # > 1 s: another process
            continue
        sid = d.get("sessionId")
        if sid:
            out[sid] = {"pid": pid, "status": d.get("status"), "name": d.get("name")}
    return out


def find(session_id):
    if not _SID.match(session_id or ""):
        raise HistoryError("不是 session id")
    base = os.path.join(home(), "projects")
    try:
        for d in os.listdir(base):
            p = os.path.join(base, d, session_id + ".jsonl")
            if os.path.isfile(p):
                return p
    except OSError:
        pass
    raise HistoryError("没有这个会话（可能已删除）")


def preview(session_id):
    """The conversation as the user saw it: their prompts, Claude's text; tool calls
    folded into one line each run of them."""
    path = find(session_id)
    msgs, tools = [], {}
    more = False

    def flush():
        if tools:
            msgs.append({"role": "tools", "text": " · ".join(f"{k} ×{v}" if v > 1 else k for k, v in tools.items())})
            tools.clear()

    with open(path, "rb") as f:
        for raw in f:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
                continue
            if len(msgs) >= MAX_PREVIEW:
                more = True
                break
            content = (o.get("message") or {}).get("content")
            if o["type"] == "user":
                cmd = _command(o)
                if cmd:
                    flush()
                    msgs.append({"role": "command", "text": cmd, "ts": _ts(o.get("timestamp"))})
                elif _is_prompt(o):
                    flush()
                    msgs.append({"role": "user", "text": _text(content).strip()[:MAX_TEXT], "ts": _ts(o.get("timestamp"))})
                continue
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    tools[c.get("name") or "tool"] = tools.get(c.get("name") or "tool", 0) + 1
                elif c.get("type") == "text" and c.get("text", "").strip():
                    flush()
                    msgs.append({"role": "assistant", "text": c["text"].strip()[:MAX_TEXT], "ts": _ts(o.get("timestamp"))})
        flush()
    return {"session": summarize(path), "messages": msgs, "more": more}


_MODEL_OK = re.compile(r"^[\w.\-\[\]]+$")


def resume_command(claude_cmd, session_id, model=None):
    """The Settings' claude command, its --continue / -c / --resume (and, when the session's
    model is known, --model) dropped, + [--model <model>] --resume <id>: the session goes on
    with the model it last ran on, not whatever the default is now."""
    words = (claude_cmd or "claude").split()
    model = model if model and _MODEL_OK.match(model) else None
    keep, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        if w in ("--continue", "-c"):
            continue
        if w in ("--resume", "-r") or (model and w == "--model"):
            skip = True
            continue
        if model and w.startswith("--model="):
            continue
        keep.append(w)
    return " ".join((keep or ["claude"]) + (["--model", model] if model else []) + ["--resume", session_id])


# --- delete / trash ---------------------------------------------------------------------

def _parts(path):
    """A session file -> every path of the session, relative to home, that exists."""
    sid = os.path.basename(path)[:-len(".jsonl")]
    d = os.path.basename(os.path.dirname(path))
    rels = [os.path.join("projects", d, sid + ".jsonl"), os.path.join("projects", d, sid)]
    rels += [os.path.join(b, sid) for b in _BESIDE]
    return [r for r in rels if os.path.exists(os.path.join(home(), r))]


def delete(session_ids, now=None):
    """Move the sessions to <home>/workbay-trash/<time>/ (same layout) -> {moved, refused}."""
    now = now or time.time()
    batch = os.path.join(home(), TRASH, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    moved, refused = [], []
    running = live()
    for sid in session_ids:
        try:
            path = find(sid)
        except HistoryError as exc:
            refused.append((sid, str(exc)))
            continue
        if sid in running:
            refused.append((sid, f"正在运行（pid {running[sid]['pid']}），先退出那个 Claude Code"))
            continue
        if now - os.path.getmtime(path) < BUSY_S:
            refused.append((sid, f"{BUSY_S // 60} 分钟内有改动，可能正在使用"))
            continue
        for rel in _parts(path):
            dst = os.path.join(batch, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(os.path.join(home(), rel), dst)
        _cache.pop(path, None)
        moved.append(sid)
    return {"moved": moved, "refused": refused, "batch": batch if moved else None}


def _batches():
    """[(name, path)] of every delete batch, the newest first; a name is unique across both trashes."""
    found = {}
    for t in (OLD_TRASH, TRASH):
        base = os.path.join(home(), t)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if os.path.isdir(os.path.join(base, n)):
                found[n] = os.path.join(base, n)
    return sorted(found.items(), reverse=True)


def trash():
    """The delete batches, the newest first: [{name, path, sessions: [summary]}]."""
    out = []
    for name, path in _batches():
        proj = os.path.join(path, "projects")
        items = []
        for d in os.listdir(proj) if os.path.isdir(proj) else []:
            for n in os.listdir(os.path.join(proj, d)):
                if n.endswith(".jsonl"):
                    try:
                        items.append(summarize(os.path.join(proj, d, n)))
                    except OSError:
                        pass
        out.append({"name": name, "path": path, "sessions": items})
    return out


def _batch(name):
    if not re.match(r"^\d{8}-\d{6}$", name or ""):
        raise HistoryError("不是回收站里的一批")
    path = dict(_batches()).get(name)
    if not path:
        raise HistoryError("回收站里没有这一批")
    return path


def restore(name):
    """Put a batch back where it was -> {restored, kept} (kept: something is there again)."""
    path = _batch(name)
    restored, kept = [], []
    for base, dirs, files in os.walk(path):
        rel = os.path.relpath(base, path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        # what was moved as a whole: projects/<d>/<file or sid dir>, file-history/<sid>, session-env/<sid>
        whole = [n for n in dirs + files if (depth == 2 and rel.split(os.sep)[0] == "projects")
                 or (depth == 1 and rel in _BESIDE)]
        for n in whole:
            src, dst = os.path.join(base, n), os.path.join(home(), rel, n)
            if os.path.exists(dst):
                kept.append(os.path.join(rel, n))
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            if n.endswith(".jsonl"):
                restored.append(n[:-len(".jsonl")])
        dirs[:] = [d for d in dirs if d not in whole]
    if not kept:
        shutil.rmtree(path, ignore_errors=True)
    return {"restored": restored, "kept": kept}


def empty_trash(name=None):
    """Delete one batch for good, or all of them."""
    if name:
        shutil.rmtree(_batch(name))
        return {"removed": [name]}
    names = [n for n, _ in _batches()]
    for t in (TRASH, OLD_TRASH):
        shutil.rmtree(os.path.join(home(), t), ignore_errors=True)
    return {"removed": names}
