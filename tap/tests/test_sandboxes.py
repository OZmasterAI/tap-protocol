"""Tests for TAP sandbox providers."""

import os
import subprocess

import pytest

from tap.agent_manager import AgentManager
from tap.constants import ISOLATION_CONTAINER, STATE_READY
from tap.sandboxes.base import ResourceLimits, SandboxHandle, SandboxProvider
from tap.sandboxes.docker import DockerProvider
from tap.sandboxes.none import NoSandboxProvider
from tap.sandboxes.podman import PodmanProvider
from tap.adapters.mock import MockAdapter


# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_defaults(self):
        rl = ResourceLimits()
        assert rl.cpu_cores == 1.0
        assert rl.memory_mb == 2048
        assert rl.timeout_s == 600
        assert rl.network is False

    def test_to_dict(self):
        rl = ResourceLimits(cpu_cores=2.0, memory_mb=4096, timeout_s=300, network=True)
        d = rl.to_dict()
        assert d == {
            "cpu_cores": 2.0,
            "memory_mb": 4096,
            "timeout_s": 300,
            "network": True,
        }

    def test_from_dict(self):
        rl = ResourceLimits.from_dict({"cpu_cores": 0.5, "memory_mb": 512})
        assert rl.cpu_cores == 0.5
        assert rl.memory_mb == 512
        assert rl.timeout_s == 600  # default

    def test_from_dict_ignores_unknown_keys(self):
        rl = ResourceLimits.from_dict({"cpu_cores": 1.0, "bogus": "ignored"})
        assert rl.cpu_cores == 1.0

    def test_roundtrip(self):
        original = ResourceLimits(
            cpu_cores=4.0, memory_mb=8192, timeout_s=120, network=True
        )
        restored = ResourceLimits.from_dict(original.to_dict())
        assert restored == original


# ---------------------------------------------------------------------------
# SandboxHandle
# ---------------------------------------------------------------------------


class TestSandboxHandle:
    def test_creation(self):
        h = SandboxHandle(
            sandbox_id="abc123", provider="docker", worktree_path="/tmp/wt"
        )
        assert h.sandbox_id == "abc123"
        assert h.provider == "docker"
        assert h.inner_path == "/workspace"
        assert h.pid is None

    def test_to_dict(self):
        h = SandboxHandle(
            sandbox_id="abc",
            provider="podman",
            worktree_path="/tmp/wt",
            inner_path="/work",
            pid=9999,
        )
        d = h.to_dict()
        assert d["sandbox_id"] == "abc"
        assert d["provider"] == "podman"
        assert d["inner_path"] == "/work"
        assert d["pid"] == 9999
        assert "resource_limits" in d


# ---------------------------------------------------------------------------
# NoSandboxProvider
# ---------------------------------------------------------------------------


class TestNoSandboxProvider:
    def test_name(self):
        assert NoSandboxProvider().name == "none"

    def test_is_available(self):
        assert NoSandboxProvider().is_available() is True

    def test_create_passthrough(self):
        p = NoSandboxProvider()
        h = p.create("agent1", "/tmp/worktree")
        assert h.sandbox_id == "host-agent1"
        assert h.provider == "none"
        assert h.inner_path == "/tmp/worktree"

    def test_exec_runs_on_host(self, tmp_path):
        p = NoSandboxProvider()
        h = p.create("agent1", str(tmp_path))
        proc = p.exec(h, ["echo", "hello"])
        stdout, _ = proc.communicate(timeout=5)
        assert "hello" in stdout

    def test_destroy_is_noop(self):
        p = NoSandboxProvider()
        h = p.create("agent1", "/tmp")
        p.destroy(h)  # should not raise


# ---------------------------------------------------------------------------
# DockerProvider (unit tests — no Docker daemon required)
# ---------------------------------------------------------------------------


class TestDockerProvider:
    def test_name(self):
        assert DockerProvider().name == "docker"

    def test_runtime_cmd(self):
        assert DockerProvider().runtime_cmd == "docker"

    def test_custom_image(self):
        p = DockerProvider(image="alpine:latest")
        assert p._image == "alpine:latest"

    def test_create_fails_without_docker(self, tmp_path):
        p = DockerProvider()
        # Override runtime to something that doesn't exist
        with pytest.raises(RuntimeError, match="Docker create failed"):
            p.create(
                "test",
                str(tmp_path),
                config={
                    "image": "nonexistent-image-zzz:latest",
                    "resource_limits": {"memory_mb": 64},
                },
            )


# ---------------------------------------------------------------------------
# PodmanProvider (unit tests — no Podman daemon required)
# ---------------------------------------------------------------------------


class TestPodmanProvider:
    def test_name(self):
        assert PodmanProvider().name == "podman"

    def test_runtime_cmd(self):
        assert PodmanProvider().runtime_cmd == "podman"

    def test_custom_image(self):
        p = PodmanProvider(image="ubuntu:22.04")
        assert p._image == "ubuntu:22.04"


# ---------------------------------------------------------------------------
# SandboxProvider ABC
# ---------------------------------------------------------------------------


class TestSandboxProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            SandboxProvider()

    def test_subclass_must_implement(self):
        class Incomplete(SandboxProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()


# ---------------------------------------------------------------------------
# AgentManager + sandbox integration
# ---------------------------------------------------------------------------


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


class TestAgentManagerSandbox:
    def test_spawn_container_without_repo_raises(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        with pytest.raises(ValueError, match="repo_dir required"):
            mgr.spawn("s1", role="tester", isolation=ISOLATION_CONTAINER)

    def test_spawn_container_with_none_provider(self, git_repo, tmp_path):
        os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
        mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
        agent = mgr.spawn(
            "s-none",
            role="tester",
            isolation=ISOLATION_CONTAINER,
            config={"sandbox": "none"},
        )
        assert agent.isolation == ISOLATION_CONTAINER
        assert agent.worktree_path is not None
        assert os.path.isdir(agent.worktree_path)
        assert agent.sandbox_handle is not None
        assert agent.sandbox_handle.provider == "none"
        assert agent.sandbox_handle.inner_path == agent.worktree_path
        assert agent.state == STATE_READY
        assert agent.alive
        mgr.kill("s-none")

    def test_kill_destroys_sandbox_and_worktree(self, git_repo, tmp_path):
        os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
        mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
        agent = mgr.spawn(
            "s-kill",
            role="tester",
            isolation=ISOLATION_CONTAINER,
            config={"sandbox": "none"},
        )
        wt_path = agent.worktree_path
        assert os.path.isdir(wt_path)
        mgr.kill("s-kill")
        assert not os.path.isdir(wt_path)
        assert mgr.get("s-kill") is None

    def test_get_sandbox_provider_docker(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        p = mgr._get_sandbox_provider({"sandbox": "docker"})
        assert p.name == "docker"

    def test_get_sandbox_provider_podman(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        p = mgr._get_sandbox_provider({"sandbox": "podman"})
        assert p.name == "podman"

    def test_get_sandbox_provider_none(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        p = mgr._get_sandbox_provider({"sandbox": "none"})
        assert p.name == "none"

    def test_get_sandbox_provider_default(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        p = mgr._get_sandbox_provider()
        assert p.name == "docker"

    def test_get_sandbox_provider_unknown_raises(self):
        mgr = AgentManager(adapter_factory=MockAdapter)
        with pytest.raises(ValueError, match="Unknown sandbox provider"):
            mgr._get_sandbox_provider({"sandbox": "qemu"})

    def test_container_agent_can_send_prompt(self, git_repo, tmp_path):
        os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
        mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
        agent = mgr.spawn(
            "s-prompt",
            role="tester",
            isolation=ISOLATION_CONTAINER,
            config={"sandbox": "none"},
        )
        ok = mgr.send_prompt("s-prompt", "ping")
        assert ok is True
        mgr.kill("s-prompt")

    def test_container_agent_can_read_output(self, git_repo, tmp_path):
        import time

        os.environ["TAP_WORKTREE_DIR"] = str(tmp_path / "worktrees")
        mgr = AgentManager(adapter_factory=MockAdapter, repo_dir=git_repo)
        mgr.spawn(
            "s-read",
            role="tester",
            isolation=ISOLATION_CONTAINER,
            config={"sandbox": "none"},
        )
        mgr.send_prompt("s-read", "hello")
        time.sleep(0.5)
        output = mgr.read_output("s-read", timeout=2.0)
        assert output is not None
        assert "hello" in output
        mgr.kill("s-read")
