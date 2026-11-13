"""ops: the buttons' implementations against real repositories."""
import os

import pytest
from conftest import commit_all, git, init_repo, make_west_ws

from workbay.ws import discover, gitstat, ops


def find(changes, path):
    return next(c for c in changes if c["path"] == path)


# --- changes / stage / commit -------------------------------------------------------

def test_changes_reports_each_kind(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "moving.txt").write_text("move me\n" * 5, encoding="utf-8")
    commit_all(work, "add moving")
    git(work, "mv", "moving.txt", "moved.txt")                     # rename
    (work / "file.txt").write_text("one\ntwo\n", encoding="utf-8")  # modified
    (work / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(work, "add", "staged.txt")
    (work / "untracked.txt").write_text("new\n", encoding="utf-8")

    rows = ops.changes(str(work))
    assert {"file.txt", "moved.txt", "staged.txt", "untracked.txt"} <= {c["path"] for c in rows}

    modified = find(rows, "file.txt")
    assert modified["unstaged"] is True and modified["staged"] is False
    assert modified["add"] == 1 and modified["del"] == 0   # "two" on top of "one"

    staged = find(rows, "staged.txt")
    assert staged["staged"] is True and staged["untracked"] is False
    assert staged["add"] == 1 and staged["del"] == 0

    untracked = find(rows, "untracked.txt")
    assert untracked["untracked"] is True and untracked["staged"] is False

    rename = find(rows, "moved.txt")
    assert rename["orig"] == "moving.txt"
    assert rename["x"] == "R"
    assert rename["staged"] is True


def test_changes_deleted_file(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "doomed.txt").write_text("bye\n", encoding="utf-8")
    commit_all(work, "add doomed")
    (work / "doomed.txt").unlink()
    row = find(ops.changes(str(work)), "doomed.txt")
    assert row["unstaged"] is True
    assert row["y"] == "D"
    assert row["del"] == 1


def test_stage_unstage_round_trip(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "file.txt").write_text("changed\n", encoding="utf-8")
    ops.stage(str(work), ["file.txt"])
    assert find(ops.changes(str(work)), "file.txt")["staged"] is True
    ops.unstage(str(work), ["file.txt"])
    row = find(ops.changes(str(work)), "file.txt")
    assert row["staged"] is False
    assert row["unstaged"] is True


@pytest.mark.parametrize("evil", ["../x", "-rf"])
def test_unsafe_paths_are_refused(remote_and_clone, evil):
    _, work, _ = remote_and_clone
    with pytest.raises(ops.OpError):
        ops.stage(str(work), [evil])
    with pytest.raises(ops.OpError):
        ops.unstage(str(work), [evil])
    with pytest.raises(ops.OpError):
        ops.diff(str(work), evil)


def test_diff_of_an_untracked_directory_lists_it(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "newdir").mkdir()
    (work / "newdir" / "one.txt").write_text("1\n", encoding="utf-8")
    text = ops.diff(str(work), "newdir", untracked=True)
    assert "未跟踪的目录" in text
    assert "one.txt" in text


def test_diff_of_a_nested_repo_says_so(remote_and_clone, tmp_path):
    _, work, _ = remote_and_clone
    inner = init_repo(work / "inner")
    (inner / "i.txt").write_text("inner\n", encoding="utf-8")
    commit_all(inner, "inner")
    text = ops.diff(str(work), os.path.join("inner"), untracked=True)
    assert "独立的 git 仓库" in text


def test_commit_needs_something_staged(remote_and_clone):
    _, work, _ = remote_and_clone
    with pytest.raises(ops.OpError):
        ops.commit(str(work), "nothing to commit")


def test_commit_returns_sha_and_strips_comments(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "file.txt").write_text("committed\n", encoding="utf-8")
    ops.stage(str(work), ["file.txt"])
    sha = ops.commit(str(work), "the subject line\n\n# a comment that must go\nbody text\n")
    assert sha and len(sha) >= 7

    subject = git(work, "log", "-1", "--format=%s").strip()
    assert subject == "the subject line"
    body = git(work, "log", "-1", "--format=%B")
    assert "# a comment that must go" not in body
    assert "body text" in body


def test_commit_risks_amend_after_push(remote_and_clone):
    _, work, _ = remote_and_clone
    assert ops.commit_risks(str(work), amend=True) != []

    (work / "local.txt").write_text("local\n", encoding="utf-8")
    commit_all(work, "only local")
    assert ops.commit_risks(str(work), amend=True) == []


def test_commit_risks_without_amend_is_always_empty(remote_and_clone):
    _, work, _ = remote_and_clone
    assert ops.commit_risks(str(work), amend=False) == []


def test_commit_template_is_read(remote_and_clone):
    _, work, _ = remote_and_clone
    template = work.parent / "template.txt"
    template.write_text("subject from template\n", encoding="utf-8")
    git(work, "config", "commit.template", str(template))
    assert ops.commit_template(str(work)) == ("subject from template\n", str(template))


def test_commit_template_without_setting(remote_and_clone):
    _, work, _ = remote_and_clone
    assert ops.commit_template(str(work)) == ("", "")


def test_commit_template_from_the_repo_file(remote_and_clone):
    """No commit.template: the repo's own .git-commit-template (Realtek repos ship one)."""
    _, work, _ = remote_and_clone
    (work / ".git-commit-template").write_text("# help\n[JIRA] ****\n", encoding="utf-8")
    text, where = ops.commit_template(str(work))
    assert text == "# help\n[JIRA] ****\n"
    assert where == os.path.join(str(work), ".git-commit-template")


def test_commit_template_setting_wins_over_the_file(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / ".git-commit-template").write_text("file\n", encoding="utf-8")
    template = work.parent / "t.txt"
    template.write_text("setting\n", encoding="utf-8")
    git(work, "config", "commit.template", str(template))
    assert ops.commit_template(str(work))[0] == "setting\n"


# --- log: refs, the upstream ---------------------------------------------------------

def test_log_carries_refs(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "tag", "v1")
    top = ops.log(str(work))[0]
    kinds = {(r["name"], r["kind"]) for r in top["refs"]}
    assert ("main", "head") in kinds
    assert ("origin/main", "remote") in kinds
    assert ("v1", "tag") in kinds
    assert not any(r["name"].endswith("/HEAD") for r in top["refs"])
    assert ops.nearest_tag(str(work)) == "v1"


def test_upstream_info_when_behind(remote_and_clone):
    _, work, seed = remote_and_clone
    (seed / "file.txt").write_text("two\n", encoding="utf-8")
    commit_all(seed, "remote moved on")
    git(seed, "push", "-q", "origin", "main")
    git(work, "fetch", "-q")
    st = gitstat.status(str(work))
    info = ops.upstream_info(str(work), st.upstream, st.behind)
    assert info["ref"] == "origin/main" and info["behind"] == 1
    assert info["commits"][0]["subject"] == "remote moved on"
    assert ops.upstream_info(str(work), st.upstream, 0) is None


# --- remotes / ssh ------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("ssh://u@h.com:29418/AI/x", ("u", "h.com", 29418)),
    ("u@h.com:AI/x", ("u", "h.com", 22)),
    ("https://h/x", None),
    ("C:/repos/x.git", None),
])
def test_gerrit_ssh(url, expected):
    assert ops.gerrit_ssh(url) == expected


# --- pull_plan ----------------------------------------------------------------------

def make_repo(abspath, name="r", cloned=True):
    return discover.Repo(name=name, path=".", abspath=str(abspath), role="project", cloned=cloned)


def test_pull_plan_skips_and_confirms(tmp_path):
    detached_repo = make_repo(tmp_path / "detached", "detached")
    noup_repo = make_repo(tmp_path / "noup", "noup")
    dirty_repo = make_repo(tmp_path / "dirty", "dirty")
    clean_repo = make_repo(tmp_path / "clean", "clean")

    statuses = {
        detached_repo.id: gitstat.RepoStatus(branch=None, upstream="origin/x", ahead=0, behind=0),
        noup_repo.id: gitstat.RepoStatus(branch="main", upstream=None),
        dirty_repo.id: gitstat.RepoStatus(branch="main", upstream="origin/main", ahead=0, behind=0, modified=1),
        clean_repo.id: gitstat.RepoStatus(branch="main", upstream="origin/main", ahead=0, behind=0),
    }
    repos = [detached_repo, noup_repo, dirty_repo, clean_repo]

    steps, skipped, need_confirm = ops.pull_plan(repos, statuses, confirm=False)
    skipped_names = {name for name, _ in skipped}
    assert skipped_names == {"detached", "noup"}
    assert [s.label for s in steps] == ["dirty", "clean"]
    assert "--autostash" in next(s for s in steps if s.label == "dirty").argv
    assert "--autostash" not in next(s for s in steps if s.label == "clean").argv
    assert any("dirty" in why for why in need_confirm)

    _, _, confirmed = ops.pull_plan(repos, statuses, confirm=True)
    assert confirmed == []


# --- west update check ---------------------------------------------------------------

def test_update_check_clean_and_local_commits(west_ws):
    ws = discover.probe(str(west_ws))
    statuses = gitstat.statuses(ws.repos)

    result = ops.update_check(ws, None, statuses)
    assert [row["name"] for row in result["rows"]] == ["projA"]
    assert result["risks"] == []
    assert "projB" not in [row["name"] for row in result["rows"]]

    (west_ws / "a" / "local.txt").write_text("local\n", encoding="utf-8")
    commit_all(west_ws / "a", "local commit")
    statuses = gitstat.statuses(ws.repos)
    result = ops.update_check(ws, None, statuses)
    assert result["risks"]
    assert any("projA" in risk for risk in result["risks"])


def test_update_check_dirty_project_is_a_risk(west_ws):
    ws = discover.probe(str(west_ws))
    (west_ws / "a" / "readme.md").write_text("dirty\n", encoding="utf-8")
    result = ops.update_check(ws, None, gitstat.statuses(ws.repos))
    assert any("projA" in risk and "未提交改动" in risk for risk in result["risks"])
    assert any("未提交改动" in note for note in result["rows"][0]["notes"])


def test_update_check_chosen_projects_filter_active_ones(west_ws):
    ws = discover.probe(str(west_ws))
    result = ops.update_check(ws, ["projB"], gitstat.statuses(ws.repos))
    assert [row["name"] for row in result["rows"]] == []      # projB is filtered out

    result = ops.update_check(ws, ["projA"], gitstat.statuses(ws.repos))
    assert [row["name"] for row in result["rows"]] == ["projA"]


# --- job steps ------------------------------------------------------------------

def make_repo_stub(tmp_path):
    return discover.Repo(name="r", path=".", abspath=str(tmp_path), role="plain", cloned=True)


def test_git_steps_accepts_the_git_prefix(tmp_path):
    repo = make_repo_stub(tmp_path)
    for text in ("git log -1", "log -1"):
        steps = ops.git_steps([repo], text)
        assert len(steps) == 1
        assert steps[0].argv == ["git", "log", "-1"]


def test_git_steps_empty_is_an_error(tmp_path):
    with pytest.raises(ops.OpError):
        ops.git_steps([make_repo_stub(tmp_path)], "   ")
    with pytest.raises(ops.OpError):
        ops.git_steps([make_repo_stub(tmp_path)], "git")


def test_git_steps_only_cloned_repos(tmp_path):
    cloned = make_repo_stub(tmp_path)
    missing = discover.Repo(name="m", path="m", abspath=str(tmp_path / "m"), role="project", cloned=False)
    assert [s.label for s in ops.git_steps([cloned, missing], "status")] == ["r"]


def test_forall_steps_empty_command():
    class StubWs:
        path = "."

    with pytest.raises(ops.OpError):
        ops.forall_steps(StubWs(), "  ", None)


def test_open_files_lists_keil_and_vscode(tmp_path):
    (tmp_path / "x.uvmpw").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.code-workspace").write_text("", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "ignored.uvmpw").write_text("", encoding="utf-8")

    found = sorted(os.path.basename(p) for p in ops.open_files(str(tmp_path)))
    assert found == ["x.uvmpw", "y.code-workspace"]


def test_open_claude_runs_the_command_in_a_new_cmd(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ops.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(ops, "tools", lambda: {"claude": "C:/x/claude.exe"})
    ops.open_with("claude", str(tmp_path), claude_cmd="claude --continue")
    (argv,), kw = calls[0]
    assert argv.startswith("cmd.exe /K ") and argv.rstrip('"').endswith("claude --continue")
    assert kw["cwd"] == str(tmp_path)


def test_open_claude_without_the_cli_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "tools", lambda: {"claude": None})
    with pytest.raises(ops.OpError, match="claude"):
        ops.open_with("claude", str(tmp_path))
