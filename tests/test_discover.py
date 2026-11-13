"""discover: which directories under a root become workspaces, and of which kind."""
import os

from conftest import commit_all, git, init_repo, make_west_ws

from apiary import discover
from apiary.config import Settings


def build_root(tmp_path):
    """root/ with a single repo, a multi repo, an empty dir, a hidden repo and a west ws."""
    root = tmp_path / "root"
    root.mkdir()

    single = init_repo(root / "single")
    (single / "s.txt").write_text("single\n", encoding="utf-8")
    commit_all(single, "single")

    multi = root / "multi"
    for rel in ("r1", "sub/r2"):
        repo = init_repo(multi / rel)
        (repo / "m.txt").write_text(rel + "\n", encoding="utf-8")
        commit_all(repo, rel)
    (multi / "sub" / "plain").mkdir()

    (root / "empty").mkdir()

    hidden = init_repo(root / ".hidden")
    (hidden / "h.txt").write_text("hidden\n", encoding="utf-8")
    commit_all(hidden, "hidden")

    make_west_ws(root / "westws", tmp_path / "remotes")
    return root


def by_name(workspaces):
    return {os.path.basename(ws.path): ws for ws in workspaces}


def test_scan_kinds(tmp_path):
    root = build_root(tmp_path)
    found = by_name(discover.scan(Settings(roots=[str(root)])))

    assert found["single"].kind == "single"
    assert found["multi"].kind == "multi"
    assert sorted(os.path.basename(p) for p in (r.path for r in found["multi"].repos)) == ["r1", "r2"]
    assert {os.path.normpath(os.path.join(found["multi"].path, r.path)) for r in found["multi"].repos} == {
        os.path.normpath(os.path.join(str(root / "multi"), "r1")),
        os.path.normpath(os.path.join(str(root / "multi"), "sub", "r2")),
    }
    assert found["westws"].kind == "west"


def test_scan_leaves_out_empty_and_hidden(tmp_path):
    root = build_root(tmp_path)
    names = by_name(discover.scan(Settings(roots=[str(root)])))
    assert "empty" not in names
    assert ".hidden" not in names


def test_west_repos_roles_and_cloned(west_ws):
    ws = discover.probe(str(west_ws))
    assert ws.kind == "west"
    assert ws.error == ""
    roles = [r.role for r in ws.repos]
    assert roles == ["manifest", "project", "project"]
    proj_a, proj_b = ws.repos[1], ws.repos[2]
    assert proj_a.name == "projA" and proj_a.role == "project" and proj_a.cloned is True
    assert proj_b.name == "projB" and proj_b.cloned is False
    assert proj_b.declared.active is False
    assert proj_a.declared.active is True


def test_hidden_setting_drops_a_workspace(tmp_path):
    root = build_root(tmp_path)
    settings = Settings(roots=[str(root)], hidden=[str(root / "multi")])
    names = by_name(discover.scan(settings))
    assert "multi" not in names
    assert "single" in names


def test_added_setting_brings_in_a_workspace_outside_the_roots(tmp_path):
    root = build_root(tmp_path)
    outside = init_repo(tmp_path / "elsewhere" / "lonely")
    (outside / "x").write_text("x", encoding="utf-8")
    commit_all(outside, "lonely")

    settings = Settings(roots=[str(root)], added=[str(outside)])
    found = by_name(discover.scan(settings))
    assert "lonely" in found
    assert found["lonely"].kind == "single"


def test_west_top_gets_role_top(west_ws):
    """The bee5 case: a west workspace whose top directory is itself a git repo."""
    init_repo(west_ws)
    (west_ws / "top.txt").write_text("top\n", encoding="utf-8")
    commit_all(west_ws, "top level repo")

    ws = discover.probe(str(west_ws))
    assert ws.kind == "west"
    assert ws.repos[0].role == "top"


def test_broken_manifest_keeps_kind_and_reports_error(west_ws):
    (west_ws / "manifest" / "west.yml").write_text("manifest: [", encoding="utf-8")
    ws = discover.probe(str(west_ws))
    assert ws.kind == "west"
    assert ws.error
    assert ws.manifest is None
    for repo in ws.repos:
        assert repo.role == "plain"


def test_plain_directory_is_not_a_workspace(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert discover.probe(str(plain)) is None
