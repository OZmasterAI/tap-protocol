"""Base sandbox provider interface for TAP."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ResourceLimits:
    """Resource constraints for a sandboxed agent."""

    cpu_cores: float = 1.0
    memory_mb: int = 2048
    timeout_s: int = 600
    network: bool = False

    def to_dict(self) -> dict:
        return {
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "timeout_s": self.timeout_s,
            "network": self.network,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ResourceLimits:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SandboxHandle:
    """Handle to a running sandbox instance."""

    sandbox_id: str
    provider: str
    worktree_path: str
    inner_path: str = "/workspace"
    pid: int | None = None
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)

    def to_dict(self) -> dict:
        return {
            "sandbox_id": self.sandbox_id,
            "provider": self.provider,
            "worktree_path": self.worktree_path,
            "inner_path": self.inner_path,
            "pid": self.pid,
            "resource_limits": self.resource_limits.to_dict(),
        }


class SandboxProvider(ABC):
    """Abstract provider for agent sandbox isolation."""

    @abstractmethod
    def create(
        self, agent_id: str, worktree_path: str, config: dict | None = None
    ) -> SandboxHandle:
        """Create a sandbox with the worktree mounted. Returns a handle."""

    @abstractmethod
    def destroy(self, handle: SandboxHandle) -> None:
        """Tear down the sandbox and clean up all resources."""

    @abstractmethod
    def exec(self, handle: SandboxHandle, cmd: list[str]) -> subprocess.Popen:
        """Run a command inside the sandbox. Returns a Popen-compatible handle."""

    def is_available(self) -> bool:
        """Check if this provider's runtime is available on the host."""
        try:
            subprocess.run(
                [self.runtime_cmd, "--version"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def runtime_cmd(self) -> str: ...
