# TAP — Toroidal Agent Protocol

Orchestrate multiple AI coding agents from a single daemon. JSON-RPC over Unix sockets, SQLite-backed task queue, adapters for any CLI agent.

## What it does

TAP is a lightweight protocol for spawning, dispatching tasks to, and monitoring multiple coding agents in parallel. Think of it as a process manager purpose-built for AI agents.

```
tap daemon                                    # start the daemon
tap spawn researcher --role researcher        # spawn an agent
tap spawn builder --role builder --model opus # spawn another
tap send researcher "find auth vulnerabilities" --priority 2
tap send builder "implement OAuth module"
tap status                                    # see all agents
tap kill researcher                           # shut one down
```

## Why not just use tmux?

| | tmux | TAP |
|---|---|---|
| Transport | Terminal PTY | JSON-RPC (structured) |
| Parse results | Screen scraping | Typed JSON messages |
| Persistence | Process stays alive | SQLite-backed crash recovery |
| Multi-agent | N panes (fragile) | First-class dispatch + coordination |
| Health monitoring | Check if pane exists | Heartbeat + auto-detect dead agents |

## Supported agents

TAP wraps any CLI that accepts stdin/stdout:

| Agent | Adapter | Status |
|---|---|---|
| Claude Code | `claude` | Tested |
| Goose | `goose` | Adapter ready |
| Pi | `pi` | Adapter ready |
| Any CLI | `generic` | Bring your own command |

## Install

```bash
pip install tap-protocol
```

Or from source:

```bash
git clone https://github.com/OZmasterAI/tap-protocol
cd tap-protocol
pip install -e .
```

## Quick start

```bash
# Start daemon (uses Claude adapter by default)
tap daemon

# In another terminal:
tap spawn my-agent --role researcher --model sonnet
tap send my-agent "what files are in this project?"
tap status
tap kill my-agent
```

## Protocol

TAP uses JSON-RPC 2.0 over Unix domain sockets (`/tmp/tap.sock` by default).

### Methods

| Namespace | Method | Purpose |
|---|---|---|
| `session/` | `spawn` | Start a new agent subprocess |
| | `kill` | Gracefully stop an agent |
| `task/` | `dispatch` | Send a task to an agent |
| | `status` | Check task progress |
| | `result` | Get task output |
| `agent/` | `heartbeat` | Check if agent is alive |
| | `context` | Get token usage, model info |
| | `list` | List all agents |
| `coord/` | `broadcast` | Message all agents |
| | `handoff` | Transfer task between agents |

### Agent modes

```bash
tap spawn worker --persistent         # stays alive across tasks (warm context)
tap spawn worker --ephemeral          # fresh subprocess per task (clean slate)
```

## Python SDK

```python
from tap.client import TAPClient

client = TAPClient()
client.spawn("researcher", role="researcher", model="sonnet")
client.send("researcher", "find security issues in auth.py")
status = client.status("researcher")
client.kill("researcher")
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `TAP_SOCKET` | `/tmp/tap.sock` | Daemon socket path |
| `TAP_DB_PATH` | `~/.tap/tap.db` | SQLite database path |

## License

MIT
