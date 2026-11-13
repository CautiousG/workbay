"""claude_history.py over a made-up ~/.claude (CLAUDE_CONFIG_DIR): list, preview, resume
command, delete to the trash and back, and the Api calls."""
import json
import os
import time

import pytest

from apiary import claude_history as ch

SID1 = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"


def rec(**kw):
    return json.dumps(kw, ensure_ascii=False)


def user(text, ts="2026-11-10T08:00:00.000Z", **kw):
    return rec(type="user", cwd=kw.pop("cwd", None), timestamp=ts, isSidechain=kw.pop("side", False),
               message={"role": "user", "content": text}, **kw)


def assistant(*content, ts="2026-11-10T08:00:05.000Z"):
    return rec(type="assistant", timestamp=ts, isSidechain=False, message={"role": "assistant", "content": list(content)})


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    ch._cache.clear()
    work = tmp_path / "work"
    work.mkdir()
    proj = home / "projects" / "D--work"
    proj.mkdir(parents=True)
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").write_text("keep me", encoding="utf-8")
    big_hook = rec(type="attachment", cwd=str(work), timestamp="2026-11-10T07:59:59.000Z", attachment={"x": "h" * 80000})
    lines = [rec(type="mode", mode="normal", sessionId=SID1), big_hook,
             user("<command-name>/model</command-name>", cwd=str(work)),
             user("看一下 humac 的构建", cwd=str(work), gitBranch="master"),
             assistant({"type": "thinking", "thinking": "..."}, {"type": "tool_use", "name": "Bash", "input": {}}),
             user([{"type": "tool_result", "content": "out"}]),
             assistant({"type": "tool_use", "name": "Bash", "input": {}}, {"type": "tool_use", "name": "Read", "input": {}}),
             user("<system-reminder>x</system-reminder>", isMeta=True),
             user("sub agent prompt", side=True),
             assistant({"type": "text", "text": "构建用的是 cmake。"}, ts="2026-11-10T09:30:00.000Z"),
             rec(type="ai-title", aiTitle="Humac cmake 构建", sessionId=SID1),
             rec(type="last-prompt", lastPrompt="看一下 humac 的构建", sessionId=SID1)]
    (proj / f"{SID1}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (proj / SID1 / "subagents").mkdir(parents=True)
    (proj / SID1 / "subagents" / "agent-a.jsonl").write_text("{}", encoding="utf-8")
    (home / "file-history" / SID1).mkdir(parents=True)
    (home / "session-env" / SID1).mkdir(parents=True)
    (proj / f"{SID2}.jsonl").write_text(rec(type="mode", mode="normal") + "\n", encoding="utf-8")
    (proj / "not-a-session.jsonl").write_text("{}", encoding="utf-8")
    (home / "history.jsonl").write_text("{}", encoding="utf-8")
    os.utime(proj / f"{SID1}.jsonl", (time.time() - 3600,) * 2)
    old = time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1))            # SID2 has no timestamps: its mtime
    os.utime(proj / f"{SID2}.jsonl", (old, old))
    return home, proj, work


def test_sessions_and_summary(claude_home):
    home, proj, work = claude_home
    ss = ch.sessions()
    assert [s["id"] for s in ss] == [SID1, SID2]                 # newest first; not-a-session skipped
    s = ss[0]
    assert s["cwd"] == str(work) and s["branch"] == "master"
    assert s["title"] == "Humac cmake 构建"
    assert s["first"] == "看一下 humac 的构建"                    # past the 80 KB hook record
    assert s["ended"] - s["started"] == 90 * 60 + 1
    assert ss[1]["first"] is None and ss[1]["title"] is None


def test_preview(claude_home):
    p = ch.preview(SID1)
    roles = [(m["role"], m["text"]) for m in p["messages"]]
    assert roles == [("command", "/model"), ("user", "看一下 humac 的构建"), ("tools", "Bash ×2 · Read"),
                     ("assistant", "构建用的是 cmake。")]
    with pytest.raises(ch.HistoryError):
        ch.preview("../x")
    with pytest.raises(ch.HistoryError):
        ch.preview("33333333-3333-3333-3333-333333333333")


def test_resume_command():
    assert ch.resume_command("claude", "S") == "claude --resume S"
    assert ch.resume_command("claude --continue --model x", "S") == "claude --model x --resume S"
    assert ch.resume_command("claude -r old -c", "S") == "claude --resume S"
    assert ch.resume_command("", "S") == "claude --resume S"
    assert ch.resume_command("claude --model sonnet -c", "S", "expert[1m]") == "claude --model expert[1m] --resume S"
    assert ch.resume_command("claude --model=x", "S", "claude-opus-5-5[1m]") == "claude --model claude-opus-5-5[1m] --resume S"
    assert ch.resume_command("claude", "S", "bad model; rm") == "claude --resume S"      # not a model id: left out


def test_delete_restore_and_empty(claude_home):
    home, proj, work = claude_home
    out = ch.delete([SID1])
    assert out["moved"] == [SID1] and out["refused"] == []
    assert not (proj / f"{SID1}.jsonl").exists() and not (proj / SID1).exists()
    assert not (home / "file-history" / SID1).exists() and not (home / "session-env" / SID1).exists()
    assert (proj / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "keep me"
    assert (home / "history.jsonl").exists()
    assert [s["id"] for s in ch.sessions()] == [SID2]
    batch = ch.trash()
    assert len(batch) == 1 and [s["id"] for s in batch[0]["sessions"]] == [SID1]
    back = ch.restore(batch[0]["name"])
    assert back == {"restored": [SID1], "kept": []}
    assert (proj / SID1 / "subagents" / "agent-a.jsonl").exists() and (home / "file-history" / SID1).exists()
    assert ch.trash() == []
    ch.delete([SID1, SID2])
    assert len(ch.trash()) == 1
    assert ch.empty_trash()["removed"]
    assert ch.trash() == [] and ch.sessions() == []
    with pytest.raises(ch.HistoryError):
        ch.restore("../../x")


def test_delete_refuses_a_session_in_use(claude_home):
    home, proj, work = claude_home
    os.utime(proj / f"{SID2}.jsonl", None)                       # just now
    out = ch.delete([SID2, "bogus"])
    assert out["moved"] == [] and len(out["refused"]) == 2
    assert (proj / f"{SID2}.jsonl").exists()


def test_restore_keeps_what_came_back(claude_home):
    home, proj, work = claude_home
    ch.delete([SID2])
    (proj / f"{SID2}.jsonl").write_text("{}", encoding="utf-8")   # a new one in the old place
    name = ch.trash()[0]["name"]
    out = ch.restore(name)
    assert out["restored"] == [] and len(out["kept"]) == 1
    assert ch.trash()[0]["name"] == name                         # stays in the trash


def test_model_and_context(claude_home):
    home, proj, work = claude_home
    usage = {"input_tokens": 2, "cache_read_input_tokens": 250_000, "cache_creation_input_tokens": 8, "output_tokens": 9}
    with open(proj / f"{SID1}.jsonl", "a", encoding="utf-8") as f:
        f.write(rec(type="assistant", requestedModel="claude-opus-5-5[1m]", isSidechain=False, timestamp="2026-11-10T10:00:00.000Z",
                    message={"model": "claude-opus-5-5", "usage": usage, "content": []}) + "\n")
        f.write(rec(type="assistant", isSidechain=True, requestedModel="claude-haiku-5-5",
                    message={"model": "claude-haiku-5-5", "usage": {"input_tokens": 5}, "content": []}) + "\n")   # a subagent: not it
    s = ch.summarize(str(proj / f"{SID1}.jsonl"))
    assert s["model"] == "claude-opus-5-5[1m]"
    assert s["context"] == {"used": 250_010, "window": 1_000_000, "pct": 25.0}
    assert ch.window("claude-sonnet-5-5") == 200_000 and ch.window("expert[1m]") == 1_000_000
    s2 = {"model": None}
    ch._usage(s2, {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"cache_read_input_tokens": 300_000}}})
    assert s2["context"]["window"] == 1_000_000                    # no requestedModel, more than 200K: was 1M
    assert s2["model"] == "claude-opus-5-5[1m]" and s2["local"] is False
    assert ch.is_local("expert[1m]") and not ch.is_local("claude-sonnet-5-5")


def write_live(home, pid, sid, proc_start):
    d = home / "sessions"
    d.mkdir(exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid, "procStart": str(proc_start),
                                               "status": "busy", "name": "x"}), encoding="utf-8")


def test_live_sessions(claude_home):
    home, proj, work = claude_home
    me = os.getpid()
    start = ch._proc_start(me)
    assert start
    write_live(home, me, SID1, start)                              # this process: live
    write_live(home, 999_999_99, SID2, start)                      # no such process
    assert set(ch.live()) == {SID1}
    write_live(home, me, SID1, start - 10**9)                      # pid reused by another program
    assert ch.live() == {}
    write_live(home, me, SID1, start)
    out = ch.delete([SID1])
    assert out["moved"] == [] and "正在运行" in out["refused"][0][1]


def test_api_claude_calls(api_env, claude_home):
    api, root, single = api_env
    home, proj, work = claude_home
    r = api.claude_sessions()
    s = next(x for x in r["sessions"] if x["id"] == SID1)
    assert s["cwd_exists"] is True and s["ws"] is None
    assert api.claude_preview(SID1)["messages"]
    ask = api.claude_delete([SID1])
    assert ask["need_confirm"]
    assert (proj / f"{SID1}.jsonl").exists()
    assert api.claude_delete([SID1], confirm=True)["moved"] == [SID1]
    batch = api.claude_trash()["batches"][0]["name"]
    assert api.claude_restore(batch)["restored"] == [SID1]
    assert api.claude_empty_trash()["need_confirm"]
    from apiary.ui.api import ApiError
    with pytest.raises(ApiError):
        api.claude_preview("nope")
    with pytest.raises(ApiError):
        api.claude_delete([])
