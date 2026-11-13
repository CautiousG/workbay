"""jobs: steps on a thread, one job per workspace, cancellation, progress lines."""
import sys
import time

import pytest

from workbay.core.jobs import Jobs, Step


def py(code):
    return [sys.executable, "-c", code]


def wait_done(job, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if job.state not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job.id} still {job.state} after {timeout}s")


def test_failed_step_and_events(workbay_home):
    jobs = Jobs()
    job = jobs.start("t", "ws1", [
        Step("s1", py("print('a');print('b')"), str(workbay_home)),
        Step("s2", py("import sys;sys.exit(3)"), str(workbay_home)),
    ])
    wait_done(job)

    assert job.state == "failed"
    assert [s.state for s in job.steps] == ["ok", "failed"]
    assert [s.rc for s in job.steps] == [0, 3]

    _, events = jobs.events_after(0, timeout=0)
    texts = [e["text"] for e in events if e["type"] == "out"]
    assert "a" in texts
    assert "b" in texts


def test_keep_going_false_skips_the_rest(workbay_home):
    jobs = Jobs()
    job = jobs.start("t", "ws-serial", [
        Step("s1", py("import sys;sys.exit(1)"), str(workbay_home)),
        Step("s2", py("import sys;sys.exit(0)"), str(workbay_home)),
        Step("s3", py("import sys;sys.exit(0)"), str(workbay_home)),
    ], keep_going=False)
    wait_done(job)

    assert [s.state for s in job.steps] == ["failed", "skipped", "skipped"]
    assert job.steps[1].rc is None
    assert job.state == "failed"


def test_busy_per_workspace(workbay_home):
    jobs = Jobs()
    first = jobs.start("slow", "same-ws", [Step("s", py("import time;time.sleep(2)"), str(workbay_home))])
    try:
        with pytest.raises(Exception) as exc:
            jobs.start("second", "same-ws", [Step("s", py("pass"), str(workbay_home))])
        assert "正在执行" in str(exc.value)

        other = jobs.start("elsewhere", "other-ws", [Step("s", py("pass"), str(workbay_home))])
        wait_done(other)
        assert other.state == "ok"
    finally:
        jobs.cancel(first.id)
    wait_done(first)


def test_cancel_stops_a_long_step(workbay_home):
    jobs = Jobs()
    job = jobs.start("sleepy", "ws-cancel", [
        Step("s", py("import time;time.sleep(30)"), str(workbay_home))])
    time.sleep(0.5)
    assert jobs.cancel(job.id) is True
    wait_done(job, timeout=10)
    assert job.state == "cancelled"


def test_progress_lines(workbay_home):
    jobs = Jobs()
    code = ("import sys\n"
            "sys.stdout.write('x\\r y\\r z\\n')\n"
            "sys.stdout.flush()\n")
    job = jobs.start("progress", "ws-progress", [Step("s", py(code), str(workbay_home))])
    wait_done(job)
    assert job.state == "ok"

    _, events = jobs.events_after(0, timeout=0)
    outs = [e for e in events if e["type"] == "out" and e["text"] in ("x", " y", " z")]
    assert outs, [e.get("text") for e in events if e["type"] == "out"]
    assert any(e["progress"] for e in outs)
    last = outs[-1]
    assert last["text"].strip() == "z"
    assert last["progress"] is False
