"""No-op sandbox provider — passthrough for host execution."""

from __future__ import annotations

import subprocess

from .base import SandboxHandle, SandboxProvider


class NoSandboxProvider(SandboxProvider):
    """Passthrough provider that runs commands directly on the host."""

    @property
    def name(self) -> str:
        return "none"

    @property
    def runtime_cmd(self) -> str:
        return "true"

    def is_available(self) -> bool:
        return True

    def create(
        self, agent_id: str, worktree_path: str, config: dict | None = None
    ) -> SandboxHandle:
        return SandboxHandle(
            sandbox_id=f"host-{agent_id}",
            provider="none",
            worktree_path=worktree_path,
            inner_path=worktree_path,
        )

    def exec(self, handle: SandboxHandle, cmd: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=handle.inner_path,
        )

    def destroy(self, handle: SandboxHandle) -> None:
        pass
