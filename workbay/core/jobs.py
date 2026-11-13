"""Long work as Jobs: a list of steps (one command each, usually one per repository) run
in order on a thread, each line of output an event. One write Job per workspace at a
time; different workspaces run side by side.

Events, all Jobs in one ordered list (the page follows it from an index over SSE):

    {"i": n, "job": id, "type": "job",  "state": queued|running|ok|failed|cancelled}
    {"i": n, "job": id, "type": "step", "step": k, "state": running|ok|failed|cancelled|skipped, "rc": int}
    {"i": n, "job": id, "type": "out",  "step": k, "text": "a line", "progress": bool}
    {"i": n, "job": id, "type": "note", "step": k, "text": "the step's one-line outcome"}

A step whose command prints a line "@@note <text>" gets that as its note (sync.py does);
a step with skip_rc exiting with it is "skipped", not "failed".
"""
import itertools
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import gitcmd

NOTE = "@@note "
MAX_EVENTS = 20000
MAX_JOBS = 100


class Busy(Exception):
    """The workspace already has a write Job going."""


@dataclass
class Step:
    label: str                 # what the page shows: the repo name, or the command
    argv: list
    cwd: str
    state: str = "queued"
    rc: int = None
    note: str = ""
    skip_rc: int = None        # this exit code means "skipped": nothing was touched


@dataclass
class Job:
    id: int
    title: str
    ws_id: str
    steps: list
    keep_going: bool = True    # a failed step does not stop the rest
    state: str = "queued"
    started: float = None
    ended: float = None
    _proc: object = field(default=None, repr=False)
    _cancel: bool = field(default=False, repr=False)
    _locks: list = field(default_factory=list, repr=False)   # the _busy keys it holds

    def as_dict(self):
        return {"id": self.id, "title": self.title, "ws_id": self.ws_id, "state": self.state,
                "started": self.started, "ended": self.ended,
                "steps": [{"label": s.label, "argv": s.argv, "cwd": s.cwd, "state": s.state, "rc": s.rc,
                           "note": s.note} for s in self.steps]}


def _kill(proc):
    """The process and its children (west update runs git under it)."""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=gitcmd.CREATE_FLAGS, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if proc.poll() is None:
        proc.kill()


class Jobs:
    def __init__(self):
        self._lock = threading.Condition()
        self._events = []           # the newest MAX_EVENTS
        self._base = 0              # index of _events[0]
        self._jobs = {}
        self._busy = {}             # ws_id -> job id
        self._ids = itertools.count(1)

    # --- events ---
    def _emit(self, **event):
        with self._lock:
            event["i"] = self._base + len(self._events)
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                drop = len(self._events) - MAX_EVENTS
                del self._events[:drop]
                self._base += drop
            self._lock.notify_all()

    def events_after(self, index, timeout=10.0):
        """-> (next index, the events from index on); waits up to timeout for one."""
        with self._lock:
            end = lambda: self._base + len(self._events)   # noqa: E731
            if index >= end() and timeout:
                self._lock.wait_for(lambda: index < end(), timeout)
            start = max(index, self._base) - self._base
            batch = self._events[start:]
            return end(), batch

    # --- jobs ---
    def busy(self, ws_id):
        with self._lock:
            return ws_id in self._busy

    def list(self):
        with self._lock:
            return [j.as_dict() for j in sorted(self._jobs.values(), key=lambda j: -j.id)]

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, title, ws_id, steps, keep_going=True, locks=()):
        """A new Job on its own thread -> it. Busy when ws_id has one going, or one of
        locks (a machine-wide thing such as "jlink", the one J-Link probe) is taken."""
        with self._lock:
            if ws_id in self._busy:
                other = self._jobs[self._busy[ws_id]]
                raise Busy(f"这个 workspace 正在执行：{other.title}")
            for name in locks:
                if "lock:" + name in self._busy:
                    other = self._jobs[self._busy["lock:" + name]]
                    raise Busy(f"{name} 正被占用：{other.title}")
            job = Job(next(self._ids), title, ws_id, steps, keep_going)
            job._locks = [ws_id] + ["lock:" + n for n in locks]
            self._jobs[job.id] = job
            for key in job._locks:
                self._busy[key] = job.id
            for old in sorted(self._jobs)[:-MAX_JOBS]:
                if self._jobs[old].state not in ("queued", "running"):
                    del self._jobs[old]
        self._emit(job=job.id, type="job", state="queued", title=title, ws_id=ws_id)
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job.id}").start()
        return job

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state not in ("queued", "running"):
                return False
            job._cancel = True
            proc = job._proc
        if proc:
            _kill(proc)
        return True

    def _run(self, job):
        job.state, job.started = "running", time.time()
        self._emit(job=job.id, type="job", state="running")
        failed = False
        try:
            for k, step in enumerate(job.steps):
                if job._cancel or (failed and not job.keep_going):
                    step.state = "cancelled" if job._cancel else "skipped"
                    self._emit(job=job.id, type="step", step=k, state=step.state, rc=None)
                    continue
                step.state = "running"
                self._emit(job=job.id, type="step", step=k, state="running", rc=None)
                step.rc = self._run_step(job, k, step)
                step.state = ("cancelled" if job._cancel else "ok" if step.rc == 0
                              else "skipped" if step.skip_rc is not None and step.rc == step.skip_rc else "failed")
                failed |= step.state == "failed"
                self._emit(job=job.id, type="step", step=k, state=step.state, rc=step.rc)
        finally:
            job.state = "cancelled" if job._cancel else ("failed" if failed else "ok")
            job.ended = time.time()
            with self._lock:
                for key in job._locks:
                    self._busy.pop(key, None)
            self._emit(job=job.id, type="job", state=job.state)

    def _run_step(self, job, k, step):
        self._emit(job=job.id, type="out", step=k, text=f"$ {' '.join(step.argv)}   ({step.cwd})")
        try:
            proc = subprocess.Popen(step.argv, cwd=step.cwd, env=gitcmd.env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    creationflags=gitcmd.CREATE_FLAGS)
        except OSError as exc:
            self._emit(job=job.id, type="out", step=k, text=f"无法启动：{exc}")
            return -1
        with self._lock:
            job._proc = proc
        if job._cancel:
            _kill(proc)
        buf = b""
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            buf = self._lines(job, k, buf + chunk)
        if buf.strip():
            self._lines(job, k, buf + b"\n")
        rc = proc.wait()
        with self._lock:
            job._proc = None
        return rc

    def _lines(self, job, k, buf):
        """Emit buf's complete lines -> the incomplete rest. A line ended by a lone \\r is
        git's progress ("Receiving objects: 42%"): progress=True, the page overwrites the
        previous progress line with it rather than adding one."""
        start = 0
        for m in _EOL.finditer(buf):
            text = buf[start:m.start()].decode("utf-8", "replace")
            start = m.end()
            if text.startswith(NOTE):
                job.steps[k].note = text[len(NOTE):].strip()
                self._emit(job=job.id, type="note", step=k, text=job.steps[k].note)
            elif text.strip():
                self._emit(job=job.id, type="out", step=k, text=text, progress=m.group() == b"\r")
        return buf[start:]


_EOL = re.compile(rb"\r\n|\n|\r(?!$)")
