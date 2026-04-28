"""Tests for TAP worktree isolation."""

import os
import subprocess
import threading

import pytest

from tap.worktree import WorktreeManager


def _init_git_repo(path: str) -> str:
    """Create a minimal git repo with one commit."""
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", path, "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", path, "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    dummy = os.path.join(path, "README.md")
    with open(dummy, "w") as f:
        f.write("# test\n")
    subprocess.run(["git", "-C", path, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", path, "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture()
def git_repo(tmp_path):
    """Provide a temporary git repo."""
    repo = str(tmp_path / "repo")
    _init_git_repo(repo)
    yield repo


@pytest.fixture()
def wt_mgr(tmp_path, git_repo):
    """WorktreeManager with a temp base dir."""
    base = str(tmp_path / "worktrees")
    mgr = WorktreeManager(base_dir=base)
    yield mgr
    mgr.cleanup_all(git_repo)


class TestWorktreeCreate:
    def test_create_returns_valid_path(self, wt_mgr, git_repo):
        path = wt_mgr.create("agent-1", git_repo)
        assert os.path.isdir(path)
        assert os.path.exists(os.path.join(path, ".git"))

    def test_create_path_contains_agent_id(self, wt_mgr, git_repo):
        path = wt_mgr.create("agent-1", git_repo)
        assert "agent-1" in path

    def test_create_worktree_has_repo_files(self, wt_mgr, git_repo):
        path = wt_mgr.create("agent-1", git_repo)
        assert os.path.exists(os.path.join(path, "README.md"))


class TestWorktreeRemove:
    def test_remove_deletes_directory(self, wt_mgr, git_repo):
        path = wt_mgr.create("agent-rm", git_repo)
        assert os.path.isdir(path)
        result = wt_mgr.remove("agent-rm", git_repo)
        assert result is True
        assert not os.path.isdir(path)

    def test_remove_nonexistent_returns_false(self, wt_mgr, git_repo):
        result = wt_mgr.remove("ghost", git_repo)
        assert result is False


class TestWorktreeList:
    def test_list_tracks_created(self, wt_mgr, git_repo):
        wt_mgr.create("a1", git_repo)
        wt_mgr.create("a2", git_repo)
        listing = wt_mgr.list()
        assert "a1" in listing
        assert "a2" in listing

    def test_list_removes_after_remove(self, wt_mgr, git_repo):
        wt_mgr.create("a1", git_repo)
        wt_mgr.remove("a1", git_repo)
        assert "a1" not in wt_mgr.list()


class TestWorktreeConcurrency:
    def test_concurrent_create_uses_flock(self, tmp_path, git_repo):
        base = str(tmp_path / "worktrees")
        mgr = WorktreeManager(base_dir=base)
        results = {}
        errors = {}

        def create_wt(agent_id):
            try:
                results[agent_id] = mgr.create(agent_id, git_repo)
            except Exception as e:
                errors[agent_id] = e

        threads = [
            threading.Thread(target=create_wt, args=(f"c-{i}",)) for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors during concurrent create: {errors}"
        assert len(results) == 4
        for agent_id, path in results.items():
            assert os.path.isdir(path)
        mgr.cleanup_all(git_repo)


class TestWorktreeCleanupAll:
    def test_cleanup_all_removes_everything(self, tmp_path, git_repo):
        base = str(tmp_path / "worktrees")
        mgr = WorktreeManager(base_dir=base)
        mgr.create("x1", git_repo)
        mgr.create("x2", git_repo)
        mgr.cleanup_all(git_repo)
        assert len(mgr.list()) == 0


class TestWorktreeIntegration:
    """End-to-end lifecycle: spawn with worktree → verify cwd → kill → verify cleanup."""

    def test_full_lifecycle(self, tmp_path, git_repo):
        from tap.adapters.mock import MockAdapter
        from tap.agent_manager import AgentManager
        from tap.constants import ISOLATION_WORKTREE

        os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
        mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)

        # 1. Spawn agent with worktree isolation
        agent = mgr.spawn("integ-1", role="tester", isolation=ISOLATION_WORKTREE)
        wt_path = agent.worktree_path
        assert wt_path is not None

        # 2. Verify agent process cwd is the worktree
        proc_cwd = os.readlink(f"/proc/{agent.process.pid}/cwd")
        assert proc_cwd == wt_path
        assert proc_cwd != git_repo

        # 3. Verify worktree has repo files
        assert os.path.exists(os.path.join(wt_path, "README.md"))

        # 4. Verify worktree branch exists in git worktree list
        result = subprocess.run(
            ["git", "-C", git_repo, "worktree", "list"],
            capture_output=True,
            text=True,
        )
        assert wt_path in result.stdout

        # 5. Kill the agent
        mgr.kill("integ-1")

        # 6. Verify worktree directory is gone
        assert not os.path.isdir(wt_path)

        # 7. Verify git worktree list no longer shows it
        result = subprocess.run(
            ["git", "-C", git_repo, "worktree", "list"],
            capture_output=True,
            text=True,
        )
        assert wt_path not in result.stdout
