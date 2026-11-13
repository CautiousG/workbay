"""ui.api: the JSON-facing layer over discover / gitstat / ops / jobs."""
import sys
import time

import pytest
from conftest import commit_all, git

from workbay.ws import ops
from workbay.core.config import Settings
from workbay.core.jobs import Step
from workbay.ui.api import Api, ApiError


def wait_job(api, job_id, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        job = api.jobs.get(int(job_id))
        if job and job.state not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def ws_kind(api, kind):
    return next(w for w in api.workspaces()["workspaces"] if w["kind"] == kind)


def repo_of(ws):
    return ws["repos"][0]


# --- workspaces and status ----------------------------------------------------------

def test_workspaces_lists_both(api_env):
    api, root, single = api_env
    listed = api.workspaces()["workspaces"]
    assert len(listed) == 2
    assert {w["kind"] for w in listed} == {"single", "west"}
    assert api.workspaces()["scanned_at"] is not None


def test_status_summary_counts(api_env):
    api, root, single = api_env
    data = api.status()
    assert "summary" in next(iter(data["status"].values()))
    for ws in api.workspaces()["workspaces"]:
        entry = data["status"][ws["id"]]
        summary = entry["summary"]
        for key in ("repos", "cloned", "dirty", "untracked", "ahead", "behind", "off_manifest",
                    "detached", "errors", "not_cloned", "attention"):
            assert key in summary
        assert summary["repos"] == len(ws["repos"])
        cloned = [r for r in ws["repos"] if r["cloned"]]
        assert summary["cloned"] == len(cloned)


def test_status_single_workspace_only(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    data = api.status([ws["id"]])
    assert list(data["status"]) == [ws["id"]]


def test_unknown_workspace_is_404(api_env):
    api, root, single = api_env
    with pytest.raises(ApiError) as exc:
        api.repo("no-such-workspace", "no-such-repo")
    assert exc.value.status == 404


# --- the repo drawer ----------------------------------------------------------------

def test_repo_detail(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    detail = api.repo(ws["id"], repo_of(ws)["id"])
    assert detail["repo"]["abspath"]
    assert detail["status"]["branch"] == "main"
    assert isinstance(detail["changes"], list)
    assert isinstance(detail["log"], list)
    assert "template" in detail and "hook" in detail


def test_diff_stage_commit_round_trip(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    repo = repo_of(ws)

    (single / "readme.md").write_text("旧内容\n新内容\n", encoding="utf-8")
    text = api.diff(ws["id"], repo["id"], "readme.md")["diff"]
    assert "+新内容" in text

    staged = api.stage(ws["id"], repo["id"], ["readme.md"])["changes"]
    assert next(c for c in staged if c["path"] == "readme.md")["staged"] is True

    staged_text = api.diff(ws["id"], repo["id"], "readme.md", staged=True)["diff"]
    assert "+新内容" in staged_text

    out = api.commit(ws["id"], repo["id"], "提交说明\n")
    assert out["sha"]
    assert api.repo(ws["id"], repo["id"])["status"]["dirty"] is False
    assert git(single, "log", "-1", "--format=%s").strip() == "提交说明"


# --- fetch ---------------------------------------------------------------------

def test_fetch_job_finishes(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    out = api.fetch(ws["id"], [repo_of(ws)["id"]])
    job = wait_job(api, out["job"]["id"])
    assert job.state == "ok"


def test_second_slow_job_on_the_same_ws_is_409(api_env, monkeypatch):
    api, root, single = api_env
    ws = ws_kind(api, "single")

    def slow_steps(repos):
        return [Step("slow", [sys.executable, "-c", "import time;time.sleep(3)"], str(root))]

    monkeypatch.setattr(ops, "fetch_steps", slow_steps)
    first = api.fetch(ws["id"])
    try:
        with pytest.raises(ApiError) as exc:
            api.fetch(ws["id"])
        assert exc.value.status == 409
    finally:
        api.jobs.cancel(first["job"]["id"])
    wait_job(api, first["job"]["id"])

    other = [w for w in api.workspaces()["workspaces"] if w["kind"] == "west"][0]
    second = api.fetch(other["id"])
    wait_job(api, second["job"]["id"])


# --- west update --------------------------------------------------------------------

def test_update_needs_confirm_with_local_commits(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "west")
    project = next(r for r in ws["repos"] if r["role"] == "project" and r["cloned"])
    a_dir = root / "westws" / project["path"]
    (a_dir / "local.txt").write_text("local\n", encoding="utf-8")
    commit_all(a_dir, "local work")

    before = len(api.jobs_list()["jobs"])
    out = api.update(ws["id"], confirm=False)
    assert "need_confirm" in out and out["need_confirm"]
    assert len(api.jobs_list()["jobs"]) == before


def test_update_check_reports_the_project(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "west")
    out = api.update_check(ws["id"])
    assert [row["name"] for row in out["rows"]] == ["projA"]


# --- manifest ------------------------------------------------------------------

def test_manifest_and_raw(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "west")

    data = api.manifest(ws["id"])
    names = [p["name"] for p in data["projects"]]
    assert "projA" in names and "projB" in names
    assert data["current"] == "west.yml"

    raw = api.manifest_raw(ws["id"])
    assert "projA" in raw["text"]

    with pytest.raises(ApiError):
        api.manifest_raw(ws["id"], file="../../etc")

    for other in api.workspaces()["workspaces"]:
        if other["kind"] != "west":
            with pytest.raises(ApiError):
                api.manifest(other["id"])


# --- open ----------------------------------------------------------------------

def test_open_refuses_files_outside_the_list(api_env, monkeypatch):
    api, root, single = api_env
    ws = ws_kind(api, "single")

    def must_not_run(*args, **kwargs):
        raise AssertionError("open_with must not be called for a refused path")

    monkeypatch.setattr(ops, "open_with", must_not_run)
    with pytest.raises(ApiError):
        api.open("explorer", ws["id"], file=str(root / "not-listed.txt"))

    assert api.open_files(ws["id"])["files"] == []


# --- settings ------------------------------------------------------------------

def test_save_settings_cleans_values(api_env):
    api, root, single = api_env
    original_terminal = api.settings.terminal
    api.save_settings({"roots": [str(root)], "refresh_s": "30", "terminal": "bogus"})
    assert api.settings.refresh_s == 30
    assert api.settings.terminal == original_terminal


def test_save_settings_roots_change_rescans(api_env):
    api, root, single = api_env
    api.workspaces()
    api.save_settings({"roots": [str(root)]})
    assert len(api.workspaces()["workspaces"]) == 2


def test_hide_drops_a_workspace(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    api.hide(ws["id"])
    assert [w["kind"] for w in api.workspaces()["workspaces"]] == ["west"]


# --- jobs / events ---------------------------------------------------------------

def test_events_after_a_job(api_env):
    api, root, single = api_env
    ws = ws_kind(api, "single")
    out = api.fetch(ws["id"])
    wait_job(api, out["job"]["id"])
    index, events = api.events_after(0, timeout=0)
    assert index > 0
    assert any(e["type"] == "job" and e["state"] == "ok" for e in events)


def test_env_reports_tools(api_env):
    api, root, single = api_env
    out = api.env()
    assert out["git"]
    assert "version" in out["west"]
    assert "gitbash" in out["tools"]
