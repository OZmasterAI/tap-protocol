"""TAP agent manager — spawn, monitor, and control agent subprocesses."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .adapters.base import BaseAdapter
from .constants import (
    DEFAULT_SANDBOX_PROVIDER,
    ISOLATION_CONTAINER,
    ISOLATION_NONE,
    ISOLATION_WORKTREE,
    MODE_EPHEMERAL,
    MODE_STREAMING,
    STATE_DEAD,
    STATE_IDLE,
    STATE_READY,
    STATE_WORKING,
    WATCHDOG_TIMEOUT,
)


@dataclass
class ManagedAgent:
    """A running agent subprocess managed by TAP."""

    agent_id: str
    role: str
    model: str
    persistent: bool
    adapter: BaseAdapter
    process: subprocess.Popen
    state: str = STATE_READY
    started_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    current_task_id: str | None = None
    isolation: str = ISOLATION_NONE
    worktree_path: str | None = None
    sandbox_handle: object | None = None
    mode: str = MODE_STREAMING
    session_id: str | None = None
    degraded_reason: str | None = None
    _result_received: bool = field(default=False, repr=False)
    _last_result_time: float | None = field(default=None, repr=False)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def uptime_s(self) -> float:
        return time.time() - self.started_at

    def touch(self) -> None:
        """Update last heartbeat timestamp."""
        self.last_heartbeat = time.time()


class AgentManager:
    """Manages the lifecycle of TAP agent subprocesses."""

    def __init__(
        self,
        adapter_factory: Callable[..., BaseAdapter] | None = None,
        repo_dir: str | None = None,
    ):
        self._agents: dict[str, ManagedAgent] = {}
        self._lock = threading.Lock()
        self._adapter_factory = adapter_factory
        self._repo_dir = repo_dir
        self._worktree_mgr: WorktreeManager | None = None
        if repo_dir:
            from .worktree import WorktreeManager

            self._worktree_mgr = WorktreeManager()

    def spawn(
        self,
        agent_id: str,
        role: str,
        model: str = "sonnet",
        adapter: BaseAdapter | None = None,
        persistent: bool = True,
        isolation: str = ISOLATION_NONE,
        config: dict | None = None,
    ) -> ManagedAgent:
        """Spawn a new agent subprocess."""
        if agent_id in self._agents:
            existing = self._agents[agent_id]
            if existing.alive:
                raise ValueError(f"Agent {agent_id} is already running")
            self._cleanup(agent_id)

        if adapter is None:
            if self._adapter_factory:
                adapter = self._adapter_factory(model=model, persistent=persistent)
            else:
                raise ValueError("No adapter provided and no adapter_factory set")

        worktree_path = None
        sandbox_handle = None

        if isolation in (ISOLATION_WORKTREE, ISOLATION_CONTAINER):
            if not self._worktree_mgr or not self._repo_dir:
                raise ValueError("repo_dir required for worktree/container isolation")
            worktree_path = self._worktree_mgr.create(agent_id, self._repo_dir)

        if isolation == ISOLATION_CONTAINER:
            provider = self._get_sandbox_provider(config)
            sandbox_handle = provider.create(agent_id, worktree_path, config)
            cmd = adapter.spawn_cmd()
            proc = provider.exec(sandbox_handle, cmd)
        else:
            cmd = adapter.spawn_cmd()
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=worktree_path,
            )

        agent = ManagedAgent(
            agent_id=agent_id,
            role=role,
            model=model,
            persistent=persistent,
            adapter=adapter,
            process=proc,
            isolation=isolation,
            worktree_path=worktree_path,
            sandbox_handle=sandbox_handle,
        )

        with self._lock:
            self._agents[agent_id] = agent
        return agent

    def kill(self, agent_id: str) -> bool:
        """Gracefully kill an agent. Returns True if killed."""
        agent = self.get(agent_id)
        if agent is None:
            return False

        if agent.alive:
            try:
                if agent.process.stdin and not agent.process.stdin.closed:
                    agent.process.stdin.close()
                agent.process.terminate()
                agent.process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                agent.process.kill()
                try:
                    agent.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        if agent.sandbox_handle:
            provider = self._get_sandbox_provider()
            provider.destroy(agent.sandbox_handle)

        if agent.worktree_path and self._worktree_mgr and self._repo_dir:
            self._worktree_mgr.remove(agent_id, self._repo_dir)

        self._cleanup(agent_id)
        return True

    def get(self, agent_id: str) -> ManagedAgent | None:
        """Get a managed agent by ID."""
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self) -> list[ManagedAgent]:
        """List all managed agents."""
        with self._lock:
            return list(self._agents.values())

    def check_heartbeats(self) -> list[str]:
        """Check all agents for dead processes. Returns list of dead agent IDs."""
        dead = []
        with self._lock:
            for agent_id, agent in self._agents.items():
                if not agent.alive and agent.state != STATE_DEAD:
                    agent.state = STATE_DEAD
                    dead.append(agent_id)
        return dead

    def send_prompt(self, agent_id: str, prompt: str) -> bool:
        """Send a prompt to an agent's stdin. Returns True on success.

        For streaming persistent agents: sends NDJSON, keeps stdin open.
        For ephemeral agents: sends plain text, closes stdin to signal EOF.
        """
        agent = self.get(agent_id)
        if agent is None or not agent.alive:
            return False

        try:
            formatted = agent.adapter.format_input(prompt)
            agent.process.stdin.write(formatted)
            agent.process.stdin.flush()
            if agent.mode == MODE_EPHEMERAL or not agent.persistent:
                # Ephemeral: close stdin to signal EOF (claude -p needs this)
                agent.process.stdin.close()
            # Reset watchdog state for new turn
            agent._result_received = False
            agent._last_result_time = None
            agent.state = STATE_WORKING
            agent.touch()
            return True
        except (OSError, BrokenPipeError):
            agent.state = STATE_DEAD
            return False

    def read_output(self, agent_id: str, timeout: float = 5.0) -> str | None:
        """Read a line from agent's stdout. Returns None on timeout/error."""
        agent = self.get(agent_id)
        if agent is None or not agent.alive:
            return None

        try:
            import select

            ready, _, _ = select.select([agent.process.stdout], [], [], timeout)
            if ready:
                line = agent.process.stdout.readline()
                if line:
                    agent.touch()
                    return agent.adapter.parse_output(line)
            return None
        except (OSError, ValueError):
            return None

    def read_all_output(self, agent_id: str, timeout: float = 30.0) -> str | None:
        """Read all output lines until timeout or process exits. Returns final result."""
        agent = self.get(agent_id)
        if agent is None:
            return None

        import select

        result = None
        deadline = time.time() + timeout

        while time.time() < deadline:
            if not agent.alive:
                # Process exited — drain remaining output
                try:
                    remaining = agent.process.stdout.read()
                    if remaining:
                        for line in remaining.splitlines():
                            parsed = agent.adapter.parse_output(line)
                            if parsed:
                                result = parsed
                except (OSError, ValueError):
                    pass
                break

            try:
                remaining_time = max(0.1, deadline - time.time())
                ready, _, _ = select.select(
                    [agent.process.stdout], [], [], min(remaining_time, 1.0)
                )
                if ready:
                    line = agent.process.stdout.readline()
                    if line:
                        agent.touch()
                        parsed = agent.adapter.parse_output(line)
                        if parsed:
                            result = parsed
                    else:
                        break  # EOF
            except (OSError, ValueError):
                break

        if result:
            agent.state = STATE_IDLE
        return result

    def _check_watchdog(self, agent_id: str) -> bool:
        """Check if a persistent streaming agent's turn has timed out.

        Returns True if the agent received a result message and has been
        silent for longer than WATCHDOG_TIMEOUT seconds.
        """
        agent = self.get(agent_id)
        if agent is None:
            return False
        if not agent._result_received:
            return False
        if agent._last_result_time is None:
            return False
        return (time.time() - agent._last_result_time) > WATCHDOG_TIMEOUT

    def _fallback_to_ephemeral(self, agent_id: str) -> bool:
        """Fallback a persistent streaming agent to ephemeral resume mode.

        Saves session_id, kills dead process, respawns in ephemeral mode
        with --resume if session_id was captured.
        """
        agent = self.get(agent_id)
        if agent is None:
            return False

        saved_session_id = agent.session_id
        saved_role = agent.role
        saved_model = agent.model

        # Kill old process if still around
        if agent.alive:
            try:
                agent.process.terminate()
                agent.process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                agent.process.kill()
                try:
                    agent.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        # Respawn with ephemeral adapter
        if self._adapter_factory:
            adapter = self._adapter_factory(model=saved_model, persistent=False)
        else:
            return False

        cmd = adapter.spawn_cmd()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        new_agent = ManagedAgent(
            agent_id=agent_id,
            role=saved_role,
            model=saved_model,
            persistent=True,  # still logically persistent, just degraded
            adapter=adapter,
            process=proc,
            mode=MODE_EPHEMERAL,
            session_id=saved_session_id,
            degraded_reason="streaming process died, fell back to ephemeral",
        )

        with self._lock:
            self._agents[agent_id] = new_agent
        return True

    def _get_sandbox_provider(self, config: dict | None = None):
        """Resolve the sandbox provider from config or default."""
        from .sandboxes import DockerProvider, NoSandboxProvider, PodmanProvider

        name = (config or {}).get("sandbox", DEFAULT_SANDBOX_PROVIDER)
        providers = {
            "docker": DockerProvider,
            "podman": PodmanProvider,
            "none": NoSandboxProvider,
        }
        cls = providers.get(name)
        if cls is None:
            raise ValueError(f"Unknown sandbox provider: {name}")
        return cls()

    def _cleanup(self, agent_id: str) -> None:
        """Remove an agent from the registry."""
        with self._lock:
            self._agents.pop(agent_id, None)
