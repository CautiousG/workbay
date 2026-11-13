"""gitstat: the porcelain parser and status() against real clones."""
from conftest import commit_all, git

from apiary import gitstat

PORCELAIN = """\
# branch.oid 1234567890abcdef1234567890abcdef12345678
# branch.head feature/x
# branch.upstream origin/feature/x
# branch.ab +2 -3
1 M. N... 100644 100644 100644 aaaa bbbb dir/staged.txt
1 .M N... 100644 100644 100644 cccc dddd dir/modified.txt
2 R. N... 100644 100644 100644 eeee ffff R100 dir/renamed.txt\tdir/orig.txt
u UU N... 100644 100644 100644 1111 2222 3333 dir/conflicted.txt
? untracked-one.txt
? dir/untracked-two.txt
"""


def test_parse_porcelain_counts_and_branch():
    st = gitstat.parse_porcelain(PORCELAIN)
    assert st.branch == "feature/x"
    assert st.head == "1234567890"
    assert st.upstream == "origin/feature/x"
    assert (st.ahead, st.behind) == (2, 3)
    assert st.staged == 2          # "1 M." and "2 R."
    assert st.modified == 1        # the "1 .M" one; the rename's y is "."
    assert st.conflicted == 1
    assert st.untracked == 2
    assert st.dirty is True


def test_parse_porcelain_detached_and_initial():
    detached = gitstat.parse_porcelain("# branch.oid aaaa\n# branch.head (detached)\n# branch.ab +0 -0\n")
    assert detached.branch is None
    initial = gitstat.parse_porcelain("# branch.oid (initial)\n# branch.head main\n")
    assert initial.branch == "main"
    assert initial.head == ""


def test_parse_porcelain_clean_is_not_dirty():
    st = gitstat.parse_porcelain("# branch.oid abc\n# branch.head main\n# branch.ab +0 -0\n")
    assert st.dirty is False
    assert st.untracked == 0


def test_status_clean_clone(remote_and_clone):
    _, work, _ = remote_and_clone
    st = gitstat.status(str(work))
    assert st.error == ""
    assert st.dirty is False
    assert (st.ahead, st.behind) == (0, 0)
    assert st.branch == "main"
    assert st.untracked == 0


def test_status_counts_modified_and_untracked(remote_and_clone):
    _, work, _ = remote_and_clone
    (work / "file.txt").write_text("changed\n", encoding="utf-8")
    st = gitstat.status(str(work))
    assert st.modified == 1
    assert st.staged == 0

    (work / "new.txt").write_text("brand new\n", encoding="utf-8")
    st = gitstat.status(str(work))
    assert st.untracked == 1
    assert st.dirty is True


def test_status_ahead_and_behind(remote_and_clone):
    _, work, seed = remote_and_clone
    (work / "local.txt").write_text("local\n", encoding="utf-8")
    commit_all(work, "local work")
    assert gitstat.status(str(work)).ahead == 1

    (seed / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    commit_all(seed, "upstream work")
    git(seed, "push", "-q", "origin", "main")
    git(work, "fetch", "-q", "origin")
    st = gitstat.status(str(work))
    assert st.ahead == 1
    assert st.behind == 1
    assert st.fetched_at is not None


def test_status_outside_a_repo_reports_error(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    st = gitstat.status(str(plain))
    assert st.error
    assert st.dirty is False


def test_status_west_project_on_manifest(west_ws):
    project = str(west_ws / "a")
    before = gitstat.status(project, manifest_rev=True)
    assert before.error == ""
    assert before.on_manifest is True
    assert before.manifest_ahead is False
    assert before.manifest_rev

    (west_ws / "a" / "local.txt").write_text("local\n", encoding="utf-8")
    commit_all(west_ws / "a", "local change")
    after = gitstat.status(project, manifest_rev=True)
    # A commit on top of manifest-rev: on_manifest stays True, but it is reported as ahead.
    assert after.on_manifest is True
    assert after.manifest_ahead is True
    assert after.manifest_rev == before.manifest_rev


def test_status_west_project_off_manifest_when_diverged(west_ws):
    """A HEAD that manifest-rev is not an ancestor of: off the manifest, not merely ahead."""
    project = west_ws / "a"
    rev = gitstat.status(str(project), manifest_rev=True).manifest_rev
    git(project, "checkout", "-q", "--orphan", "side")
    (project / "side.txt").write_text("side\n", encoding="utf-8")
    commit_all(project, "unrelated root")

    st = gitstat.status(str(project), manifest_rev=True)
    assert st.manifest_rev == rev
    assert st.on_manifest is False
    assert st.manifest_ahead is False
