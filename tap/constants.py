"""TAP constants and defaults."""

import os

SOCKET_PATH = os.environ.get("TAP_SOCKET", "/tmp/tap.sock")
HEARTBEAT_INTERVAL = 5  # seconds
HEARTBEAT_TIMEOUT = 10  # seconds before marking agent dead
DEFAULT_MODEL = "sonnet"
DEFAULT_PRIORITY = 5
MAX_AGENTS = 20
DEFAULT_ADAPTER = "claude"
WATCHDOG_TIMEOUT = int(os.environ.get("TAP_WATCHDOG_TIMEOUT", "30"))

# Isolation modes
ISOLATION_NONE = "none"
ISOLATION_WORKTREE = "worktree"
ISOLATION_CONTAINER = "container"
WORKTREE_BASE_DIR = os.environ.get("TAP_WORKTREE_DIR", "/tmp/tap-worktrees")

# Sandbox defaults
DEFAULT_SANDBOX_PROVIDER = os.environ.get("TAP_SANDBOX_PROVIDER", "docker")
DEFAULT_SANDBOX_IMAGE = os.environ.get("TAP_SANDBOX_IMAGE", "node:22-bookworm")

# Agent modes
MODE_STREAMING = "streaming"
MODE_EPHEMERAL = "ephemeral"

# Agent states
STATE_READY = "ready"
STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_DEAD = "dead"

# Task stream event types
EVENT_TEXT = "text"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PROGRESS = "progress"
