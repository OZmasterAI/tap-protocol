"""Tests for TAP agent manager."""

import os
import subprocess
import time

import pytest

from tap.adapters.mock import MockAdapter
from tap.agent_manager import AgentManager
from tap.constants import ISOLATION_WORKTREE, STATE_DEAD, STATE_READY, STATE_WORKING


def _make_mgr():
    return AgentManager(adapter_factory=MockAdapter)


def test_spawn_agent():
    mgr = _make_mgr()
    agent = mgr.spawn("test-1", role="tester")
    assert agent.state == STATE_READY
    assert agent.process.pid > 0
    assert agent.alive
    mgr.kill("test-1")


def test_spawn_persistent():
    mgr = _make_mgr()
    agent = mgr.spawn("test-p", role="tester", persistent=True)
    assert agent.persistent is True
    mgr.kill("test-p")


def test_spawn_ephemeral():
    mgr = _make_mgr()
    agent = mgr.spawn("test-e", role="tester", persistent=False)
    assert agent.persistent is False
    mgr.kill("test-e")


def test_kill_agent():
    mgr = _make_mgr()
    mgr.spawn("test-k", role="tester")
    assert mgr.get("test-k") is not None
    mgr.kill("test-k")
    assert mgr.get("test-k") is None


def test_kill_nonexistent():
    mgr = _make_mgr()
    assert mgr.kill("nonexistent") is False


def test_heartbeat_detects_dead():
    mgr = _make_mgr()
    agent = mgr.spawn("test-d", role="tester")
    agent.process.kill()
    agent.process.wait()
    dead = mgr.check_heartbeats()
    assert "test-d" in dead
    assert mgr.get("test-d").state == STATE_DEAD
    mgr.kill("test-d")


def test_list_agents():
    mgr = _make_mgr()
    mgr.spawn("a1", role="r1")
    mgr.spawn("a2", role="r2")
    agents = mgr.list_agents()
    assert len(agents) == 2
    ids = {a.agent_id for a in agents}
    assert ids == {"a1", "a2"}
    mgr.kill("a1")
    mgr.kill("a2")


def test_send_prompt():
    mgr = _make_mgr()
    mgr.spawn("test-s", role="tester")
    ok = mgr.send_prompt("test-s", "hello world")
    assert ok is True
    assert mgr.get("test-s").state == STATE_WORKING
    mgr.kill("test-s")


def test_send_prompt_dead_agent():
    mgr = _make_mgr()
    assert mgr.send_prompt("nonexistent", "hello") is False


def test_read_output():
    mgr = _make_mgr()
    mgr.spawn("test-r", role="tester")
    mgr.send_prompt("test-r", "ping")
    time.sleep(0.5)
    output = mgr.read_output("test-r", timeout=2.0)
    assert output is not None
    assert "ping" in output
    mgr.kill("test-r")


def test_spawn_duplicate_alive_raises():
    mgr = _make_mgr()
    mgr.spawn("dup", role="tester")
    try:
        mgr.spawn("dup", role="tester")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    mgr.kill("dup")


def test_spawn_over_dead_agent():
    mgr = _make_mgr()
    agent = mgr.spawn("respawn", role="tester")
    agent.process.kill()
    agent.process.wait()
    # Should be able to respawn
    new_agent = mgr.spawn("respawn", role="tester")
    assert new_agent.alive
    mgr.kill("respawn")


def _init_git_repo(path: str) -> str:
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
    repo = str(tmp_path / "repo")
    _init_git_repo(repo)
    yield repo


def test_spawn_with_worktree_isolation(git_repo, tmp_path):
    os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
    mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
    agent = mgr.spawn("wt-1", role="tester", isolation=ISOLATION_WORKTREE)
    assert agent.worktree_path is not None
    assert os.path.isdir(agent.worktree_path)
    assert agent.isolation == ISOLATION_WORKTREE
    mgr.kill("wt-1")


def test_spawn_worktree_sets_cwd(git_repo, tmp_path):
    os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
    mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
    agent = mgr.spawn("wt-cwd", role="tester", isolation=ISOLATION_WORKTREE)
    # The mock adapter runs python -c "..." — check /proc/<pid>/cwd
    proc_cwd = os.readlink(f"/proc/{agent.process.pid}/cwd")
    assert proc_cwd == agent.worktree_path
    mgr.kill("wt-cwd")


def test_kill_removes_worktree(git_repo, tmp_path):
    os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
    mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
    agent = mgr.spawn("wt-rm", role="tester", isolation=ISOLATION_WORKTREE)
    wt_path = agent.worktree_path
    assert os.path.isdir(wt_path)
    mgr.kill("wt-rm")
    assert not os.path.isdir(wt_path)


def test_spawn_without_repo_dir_raises():
    mgr = AgentManager(adapter_factory=MockAdapter)
    with pytest.raises(ValueError, match="repo_dir required"):
        mgr.spawn("wt-err", role="tester", isolation=ISOLATION_WORKTREE)
