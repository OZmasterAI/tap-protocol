"""TAP worktree manager — git worktree isolation for agents."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import threading

from .constants import WORKTREE_BASE_DIR


class WorktreeManager:
    """Manage git worktrees for agent isolation.

    Uses fcntl.flock to serialize concurrent git worktree operations.
    """

    def __init__(self, base_dir: str = WORKTREE_BASE_DIR):
        self._base_dir = base_dir
        self._worktrees: dict[str, str] = {}
        self._lock = threading.Lock()

    def _worktree_path(self, agent_id: str) -> str:
        return os.path.join(self._base_dir, f"tap-{agent_id}")

    def _lock_path(self) -> str:
        os.makedirs(self._base_dir, exist_ok=True)
        return os.path.join(self._base_dir, ".tap-worktree.lock")

    def create(self, agent_id: str, repo_dir: str) -> str:
        """Create a git worktree for an agent. Returns the worktree path."""
        wt_path = self._worktree_path(agent_id)
        branch = f"tap-{agent_id}"

        lock_file = self._lock_path()
        fd = open(lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.makedirs(self._base_dir, exist_ok=True)
            subprocess.run(
                ["git", "-C", repo_dir, "worktree", "add", "-b", branch, wt_path],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

        with self._lock:
            self._worktrees[agent_id] = wt_path
        return wt_path

    def remove(self, agent_id: str, repo_dir: str) -> bool:
        """Remove a git worktree. Returns True if removed."""
        with self._lock:
            wt_path = self._worktrees.get(agent_id)
            if wt_path is None:
                return False

        lock_file = self._lock_path()
        fd = open(lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            subprocess.run(
                ["git", "-C", repo_dir, "worktree", "remove", "--force", wt_path],
                check=True,
                capture_output=True,
                text=True,
            )
            # Clean up the branch
            branch = f"tap-{agent_id}"
            subprocess.run(
                ["git", "-C", repo_dir, "branch", "-D", branch],
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            if os.path.isdir(wt_path):
                shutil.rmtree(wt_path)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

        with self._lock:
            self._worktrees.pop(agent_id, None)
        return True

    def list(self) -> dict[str, str]:
        """Return a mapping of agent_id -> worktree_path."""
        with self._lock:
            return dict(self._worktrees)

    def cleanup_all(self, repo_dir: str) -> None:
        """Remove all TAP worktrees."""
        with self._lock:
            agent_ids = list(self._worktrees.keys())
        for agent_id in agent_ids:
            self.remove(agent_id, repo_dir)
