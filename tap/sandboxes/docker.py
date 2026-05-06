"""Docker sandbox provider for TAP."""

from __future__ import annotations

import subprocess

from .base import ResourceLimits, SandboxHandle, SandboxProvider


class DockerProvider(SandboxProvider):
    """Run agents inside Docker containers with worktree bind-mounts."""

    def __init__(self, image: str = "node:22-bookworm"):
        self._image = image

    @property
    def name(self) -> str:
        return "docker"

    @property
    def runtime_cmd(self) -> str:
        return "docker"

    def create(
        self, agent_id: str, worktree_path: str, config: dict | None = None
    ) -> SandboxHandle:
        config = config or {}
        limits = ResourceLimits.from_dict(config.get("resource_limits", {}))
        image = config.get("image", self._image)
        container_name = f"tap-{agent_id}"

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-v",
            f"{worktree_path}:/workspace",
            "--cpus",
            str(limits.cpu_cores),
            "--memory",
            f"{limits.memory_mb}m",
        ]

        if not limits.network:
            cmd.extend(["--network", "none"])

        cmd.extend([image, "sleep", "infinity"])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Docker create failed: {result.stderr.strip()}")

        container_id = result.stdout.strip()

        return SandboxHandle(
            sandbox_id=container_id,
            provider="docker",
            worktree_path=worktree_path,
            inner_path="/workspace",
            resource_limits=limits,
        )

    def exec(self, handle: SandboxHandle, cmd: list[str]) -> subprocess.Popen:
        docker_cmd = [
            "docker",
            "exec",
            "-i",
            "-w",
            handle.inner_path,
            handle.sandbox_id,
        ] + cmd

        return subprocess.Popen(
            docker_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def destroy(self, handle: SandboxHandle) -> None:
        subprocess.run(
            ["docker", "rm", "-f", handle.sandbox_id],
            capture_output=True,
            timeout=15,
        )
