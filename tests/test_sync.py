"""sync.py (sync / align one repo), stuck detection, unstick, the yml switch, and the
Api calls over them - all on real repositories."""
import configparser
import os
import time

import pytest
from conftest import commit_all, git

from workbay.ws import discover, gitstat, ops, sync
from workbay.core.jobs import Jobs
from workbay.ws.manifest import Declared


def quiet(*_):
    pass


def push_change(seed, name, text, message):
    (seed / name).write_text(text, encoding="utf-8")
    commit_all(seed, message)
    git(seed, "push", "-q", "origin", "HEAD")


def head(path):
    return git(path, "rev-parse", "HEAD").strip()


def backups(path):
    return git(path, "for-each-ref", "--format=%(refname)", "refs/workbay/backup/").split()


# --- sync ---------------------------------------------------------------------------

def test_sync_rebases_the_branch_onto_the_remote(remote_and_clone):
    _, work, seed = remote_and_clone
    push_change(seed, "file.txt", "two\n", "remote c2")
    (work / "mine.txt").write_text("mine\n", encoding="utf-8")
    commit_all(work, "local")
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.OK, note
    assert git(work, "log", "-1", "--format=%s", "HEAD~1").strip() == "remote c2"
    assert "1 个本地 commit" in note
    assert git(work, "rev-parse", "manifest-rev").strip() == git(work, "rev-parse", "origin/main").strip()


def test_sync_switches_from_detached_to_the_branch(remote_and_clone):
    _, work, seed = remote_and_clone
    push_change(seed, "file.txt", "two\n", "remote c2")
    git(work, "checkout", "-q", "--detach", "HEAD")
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.OK, note
    assert git(work, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert head(work) == git(work, "rev-parse", "origin/main").strip()


def test_sync_creates_a_missing_local_branch(remote_and_clone):
    _, work, seed = remote_and_clone
    git(seed, "checkout", "-q", "-b", "dev")
    push_change(seed, "dev.txt", "dev\n", "on dev")
    code, note = sync.sync(str(work), "origin", "dev", out=quiet)
    assert code == sync.OK, note
    assert git(work, "symbolic-ref", "--short", "HEAD").strip() == "dev"
    assert git(work, "rev-parse", "--abbrev-ref", "dev@{upstream}").strip() == "origin/dev"


def test_sync_skips_a_dirty_repo_untouched(remote_and_clone):
    _, work, seed = remote_and_clone
    push_change(seed, "other.txt", "x\n", "remote c2")
    before = head(work)
    (work / "file.txt").write_text("edited\n", encoding="utf-8")
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.SKIPPED
    assert head(work) == before
    assert (work / "file.txt").read_text(encoding="utf-8") == "edited\n"


def test_sync_autostash_brings_the_changes_back(remote_and_clone):
    _, work, seed = remote_and_clone
    push_change(seed, "other.txt", "x\n", "remote c2")
    (work / "file.txt").write_text("edited\n", encoding="utf-8")
    code, note = sync.sync(str(work), "origin", "main", autostash=True, out=quiet)
    assert code == sync.OK, note
    assert head(work) == git(work, "rev-parse", "origin/main").strip()
    assert (work / "file.txt").read_text(encoding="utf-8") == "edited\n"
    assert git(work, "stash", "list").strip() == ""


def test_sync_conflict_rolls_back(remote_and_clone):
    _, work, seed = remote_and_clone
    push_change(seed, "file.txt", "theirs\n", "remote edit")
    (work / "file.txt").write_text("mine\n", encoding="utf-8")
    commit_all(work, "local edit")
    before = head(work)
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.FAILED
    assert "回滚" in note
    assert head(work) == before
    assert gitstat.in_progress(str(work)) is None
    assert git(work, "status", "--porcelain").strip() == ""


def test_sync_to_a_tag_detaches(remote_and_clone):
    _, work, seed = remote_and_clone
    git(seed, "tag", "v1")
    git(seed, "push", "-q", "origin", "v1")
    code, note = sync.sync(str(work), "origin", "refs/tags/v1", out=quiet)
    assert code == sync.OK, note
    assert git(work, "branch", "--show-current").strip() == ""
    assert head(work) == git(seed, "rev-parse", "v1").strip()


def test_sync_backs_up_commits_only_on_a_detached_head(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "checkout", "-q", "--detach", "HEAD")
    (work / "lost.txt").write_text("x\n", encoding="utf-8")
    commit_all(work, "only here")
    orphan = head(work)
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.OK, note
    refs = backups(work)
    assert len(refs) == 1
    assert git(work, "rev-parse", refs[0]).strip() == orphan


def test_sync_refuses_while_a_rebase_is_going(remote_and_clone):
    _, work, seed = remote_and_clone
    make_rebase_conflict(work, seed)
    code, note = sync.sync(str(work), "origin", "main", out=quiet)
    assert code == sync.SKIPPED
    assert "rebase" in note


def test_sync_bad_revision_fails(remote_and_clone):
    _, work, _ = remote_and_clone
    code, note = sync.sync(str(work), "origin", "no-such-branch", out=quiet)
    assert code == sync.FAILED


# --- align --------------------------------------------------------------------------

def make_rebase_conflict(work, seed):
    push_change(seed, "file.txt", "theirs\n", "remote edit")
    (work / "file.txt").write_text("mine\n", encoding="utf-8")
    commit_all(work, "local edit")
    git(work, "fetch", "-q")
    with pytest.raises(Exception):
        git(work, "rebase", "origin/main")
    assert gitstat.in_progress(str(work)) == "rebase"


def test_align_gets_out_of_anything_and_keeps_it(remote_and_clone):
    _, work, seed = remote_and_clone
    make_rebase_conflict(work, seed)
    local = git(work, "rev-parse", "main").strip()
    code, note = sync.align(str(work), "origin", "main", out=quiet)
    assert code == sync.OK, note
    assert gitstat.in_progress(str(work)) is None
    assert git(work, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert head(work) == git(work, "rev-parse", "origin/main").strip()
    assert local in [git(work, "rev-parse", r).strip() for r in backups(work)]


def test_align_stashes_changes(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "file.txt").write_text("edited\n", encoding="utf-8")
    code, note = sync.align(str(work), "origin", "main", out=quiet)
    assert code == sync.OK, note
    assert git(work, "status", "--porcelain", "--untracked-files=no").strip() == ""
    assert "workbay align" in git(work, "stash", "list")
    assert backups(work) == []                     # HEAD was on origin/main: nothing to keep


# --- stuck / unstick ------------------------------------------------------------------

def declared(revision="main"):
    return Declared(name="p", path="p", abspath="p", revision=revision, url="", groups=[], active=True)


def test_stuck_kinds(remote_and_clone):
    _, work, seed = remote_and_clone
    st = gitstat.status(str(work))
    assert ops.stuck(st, declared()) is None
    assert ops.stuck(st, declared("dev"))["kind"] == "other_branch"
    assert ops.stuck(st, declared("refs/tags/v1")) is None      # a tag: any HEAD is fine to sync
    git(work, "checkout", "-q", "--detach", "HEAD")
    assert ops.stuck(gitstat.status(str(work)), declared())["kind"] == "detached"
    git(work, "checkout", "-q", "main")
    make_rebase_conflict(work, seed)
    s = ops.stuck(gitstat.status(str(work)), None)
    assert s["kind"] == "in_progress"
    assert {"abort", "continue", "skip", "align"} <= set(s["fixes"])


def test_unstick_abort(remote_and_clone):
    _, work, seed = remote_and_clone
    make_rebase_conflict(work, seed)
    ops.unstick(str(work), "abort")
    assert gitstat.in_progress(str(work)) is None
    with pytest.raises(ops.OpError):
        ops.unstick(str(work), "abort")             # nothing going on now


def test_unstick_continue_needs_the_conflict_resolved(remote_and_clone):
    _, work, seed = remote_and_clone
    make_rebase_conflict(work, seed)
    with pytest.raises(ops.OpError):
        ops.unstick(str(work), "continue")
    (work / "file.txt").write_text("merged\n", encoding="utf-8")
    git(work, "add", "file.txt")
    ops.unstick(str(work), "continue")
    assert gitstat.in_progress(str(work)) is None
    assert (work / "file.txt").read_text(encoding="utf-8") == "merged\n"


def test_backup_restore_and_drop(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "update-ref", "refs/workbay/backup/20260101-000000-abc", "HEAD")
    assert [b["ref"] for b in ops.backups(str(work))] == ["refs/workbay/backup/20260101-000000-abc"]
    name = ops.restore_backup(str(work), "refs/workbay/backup/20260101-000000-abc")
    assert git(work, "rev-parse", name).strip() == head(work)
    ops.drop_backup(str(work), "refs/workbay/backup/20260101-000000-abc")
    assert ops.backups(str(work)) == []
    with pytest.raises(ops.OpError):
        ops.restore_backup(str(work), "refs/heads/main")


def test_backups_kept_under_the_old_name(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "update-ref", "refs/apiary/backup/20260101-000000-old", "HEAD")      # from when it was Apiary
    git(work, "update-ref", "refs/workbay/backup/20260202-000000-new", "HEAD")
    assert [b["ref"] for b in ops.backups(str(work))] == ["refs/workbay/backup/20260202-000000-new",
                                                          "refs/apiary/backup/20260101-000000-old"]
    assert git(work, "rev-parse", ops.restore_backup(str(work), "refs/apiary/backup/20260101-000000-old")).strip() == head(work)
    ops.drop_backup(str(work), "refs/apiary/backup/20260101-000000-old")
    assert len(ops.backups(str(work))) == 1


# --- the west workspace: plan, the yml switch, the Api ---------------------------------

def test_sync_plan_on_a_west_ws(west_ws):
    ws = discover.probe(str(west_ws))
    sts = gitstat.statuses(ws.repos)
    plan = ops.sync_plan(ws, None, sts)
    rows = {r["name"]: r for r in plan["rows"]}
    assert set(rows) == {"projA"}                    # projB: filtered out by the group filter
    assert rows["projA"]["kind"] == "branch" and not rows["projA"]["skip"]
    (west_ws / "a" / "readme.md").write_text("edit\n", encoding="utf-8")
    sts = gitstat.statuses(ws.repos)
    assert ops.sync_plan(ws, None, sts)["rows"][0]["skip"] is True
    plan = ops.sync_plan(ws, None, sts, {"projA"})
    assert plan["rows"][0]["skip"] is False and plan["risks"]
    steps = ops.sync_steps(plan, ws)
    assert "--autostash" in steps[0].argv and steps[0].skip_rc == 2


def test_set_manifest_file(west_ws):
    other = west_ws / "manifest" / "other.yml"
    other.write_text((west_ws / "manifest" / "west.yml").read_text(encoding="utf-8"), encoding="utf-8")
    ws = discover.probe(str(west_ws))
    ops.set_manifest_file(ws.path, ws.manifest.repo, "other.yml")
    cfg = configparser.ConfigParser()
    cfg.read(west_ws / ".west" / "config", encoding="utf-8")
    assert cfg.get("manifest", "file") == "other.yml"
    assert cfg.get("manifest", "path") == "manifest"        # the rest kept
    assert discover.probe(str(west_ws)).manifest.file.endswith("other.yml")
    for bad in ("../x.yml", "missing.yml", "west.txt"):
        with pytest.raises(ops.OpError):
            ops.set_manifest_file(ws.path, ws.manifest.repo, bad)


def wait(api, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = api.jobs.get(int(job_id))
        if job.state not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not end")


def test_api_sync_job_reports_notes(api_env):
    api, root, _ = api_env
    ws = next(w for w in api.workspaces()["workspaces"] if w["kind"] == "west")
    a = root / "westws" / "a"
    git(a, "checkout", "-q", "--detach", "HEAD")
    st = api.status([ws["id"]])["status"][ws["id"]]
    projA = next(r for r in ws["repos"] if r["name"] == "projA")
    assert st["repos"][projA["id"]]["stuck"]["kind"] == "detached"
    out = api.sync(ws["id"])
    job = wait(api, out["job"]["id"])
    assert job.state == "ok", [(s.label, s.state, s.note) for s in job.steps]
    assert job.steps[0].note.startswith("main = origin/main")
    assert git(a, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert api.stuck(ws["id"])["stuck"] == {}


def test_api_align_asks_first(api_env):
    api, root, _ = api_env
    ws = next(w for w in api.workspaces()["workspaces"] if w["kind"] == "west")
    projA = next(r for r in ws["repos"] if r["name"] == "projA")
    (root / "westws" / "a" / "readme.md").write_text("edit\n", encoding="utf-8")
    ask = api.align(ws["id"], [projA["id"]])
    assert "stash" in ask["need_confirm"][0]
    job = wait(api, api.align(ws["id"], [projA["id"]], confirm=True)["job"]["id"])
    assert job.state == "ok", job.steps[0].note


def test_api_repo_detail_has_the_new_fields(api_env):
    api, root, single = api_env
    ws = next(w for w in api.workspaces()["workspaces"] if w["kind"] == "single")
    (single / ".git-commit-template").write_text("[JIRA] ****\n", encoding="utf-8")
    d = api.repo(ws["id"], ws["repos"][0]["id"])
    assert d["template"] == "[JIRA] ****\n"
    assert d["template_file"].endswith(".git-commit-template")
    assert d["stuck"] is None and d["backups"] == [] and d["stashes"] == 0
    assert any(r["kind"] == "remote" for r in d["log"][0]["refs"])


def test_jobs_note_and_skip_rc(workbay_home, tmp_path):
    import sys
    from workbay.core.jobs import Step
    jobs = Jobs()
    code = "import sys; print('working'); print('@@note all fine'); sys.exit(int(sys.argv[1]))"
    job = jobs.start("t", "ws", [Step("a", [sys.executable, "-c", code, "0"], str(tmp_path), skip_rc=2),
                                 Step("b", [sys.executable, "-c", code, "2"], str(tmp_path), skip_rc=2)])
    end = time.time() + 30
    while job.state in ("queued", "running") and time.time() < end:
        time.sleep(0.05)
    assert [s.state for s in job.steps] == ["ok", "skipped"]
    assert job.state == "ok"
    assert job.steps[0].note == "all fine"
    _, events = jobs.events_after(0, timeout=0)
    # the note line is not output (the "$ <argv>" echo of the command has it, that is all)
    assert not any(e["type"] == "out" and e["text"].startswith("@@note") for e in events)
    assert [e["text"] for e in events if e["type"] == "note"] == ["all fine", "all fine"]
