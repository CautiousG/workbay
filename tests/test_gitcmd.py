"""gitcmd: reading .git's files without a git process."""
import pytest
from conftest import commit_all, git

from workbay.core import gitcmd


def test_is_repo_dir_plain_repo(remote_and_clone):
    _, work, _ = remote_and_clone
    assert gitcmd.is_repo_dir(str(work)) is True


def test_is_repo_dir_rejects_tool_leftovers(tmp_path):
    fake = tmp_path / "not-a-repo"
    (fake / ".git" / "gk").mkdir(parents=True)
    assert gitcmd.is_repo_dir(str(fake)) is False


def test_is_repo_dir_accepts_worktree_gitdir_file(remote_and_clone):
    _, work, _ = remote_and_clone
    linked = work.parent / "linked"
    git(work, "worktree", "add", "-q", str(linked), "-b", "linked-branch")
    assert (linked / ".git").is_file()
    assert (linked / ".git").read_text(encoding="utf-8").startswith("gitdir: ")
    assert gitcmd.is_repo_dir(str(linked)) is True


def test_is_repo_dir_missing_path(tmp_path):
    assert gitcmd.is_repo_dir(str(tmp_path / "nothing-here")) is False


def test_read_ref_matches_rev_parse(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "branch", "foo")
    expected = git(work, "rev-parse", "foo").strip()
    assert gitcmd.read_ref(str(work), "refs/heads/foo") == expected


def test_read_ref_survives_pack_refs(remote_and_clone):
    _, work, _ = remote_and_clone
    git(work, "branch", "foo")
    expected = git(work, "rev-parse", "foo").strip()
    git(work, "pack-refs", "--all")
    assert not (work / ".git" / "refs" / "heads" / "foo").exists()
    assert gitcmd.read_ref(str(work), "refs/heads/foo") == expected


def test_read_ref_missing_ref_is_none(remote_and_clone):
    _, work, _ = remote_and_clone
    assert gitcmd.read_ref(str(work), "refs/heads/no-such-branch") is None


def test_read_ref_outside_a_repo_is_none(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitcmd.read_ref(str(plain), "refs/heads/main") is None


def test_run_rc_returns_the_exit_code(remote_and_clone):
    _, work, _ = remote_and_clone
    assert gitcmd.run(["rev-parse", "--verify", "-q", "HEAD"], str(work), rc=True) == 0
    assert gitcmd.run(["rev-parse", "--verify", "-q", "nope"], str(work), rc=True) != 0


def test_run_raises_git_error_on_a_bad_command(remote_and_clone):
    _, work, _ = remote_and_clone
    with pytest.raises(gitcmd.GitError):
        gitcmd.run(["rev-parse", "--verify", "nope"], str(work))
