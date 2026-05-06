"""Podman sandbox provider for TAP."""

from __future__ import annotations

import subprocess

from .base import ResourceLimits, SandboxHandle, SandboxProvider


class PodmanProvider(SandboxProvider):
    """Run agents inside Podman containers (rootless, daemonless)."""

    def __init__(self, image: str = "node:22-bookworm"):
        self._image = image

    @property
    def name(self) -> str:
        return "podman"

    @property
    def runtime_cmd(self) -> str:
        return "podman"

    def create(
        self, agent_id: str, worktree_path: str, config: dict | None = None
    ) -> SandboxHandle:
        config = config or {}
        limits = ResourceLimits.from_dict(config.get("resource_limits", {}))
        image = config.get("image", self._image)
        container_name = f"tap-{agent_id}"

        cmd = [
            "podman",
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
            "--userns=keep-id",
        ]

        if not limits.network:
            cmd.extend(["--network", "none"])

        cmd.extend([image, "sleep", "infinity"])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Podman create failed: {result.stderr.strip()}")

        container_id = result.stdout.strip()

        return SandboxHandle(
            sandbox_id=container_id,
            provider="podman",
            worktree_path=worktree_path,
            inner_path="/workspace",
            resource_limits=limits,
        )

    def exec(self, handle: SandboxHandle, cmd: list[str]) -> subprocess.Popen:
        podman_cmd = [
            "podman",
            "exec",
            "-i",
            "-w",
            handle.inner_path,
            handle.sandbox_id,
        ] + cmd

        return subprocess.Popen(
            podman_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def destroy(self, handle: SandboxHandle) -> None:
        subprocess.run(
            ["podman", "rm", "-f", handle.sandbox_id],
            capture_output=True,
            timeout=15,
        )
