# Implementation Plan: Worktree Isolation for TAP

## Design Decision
Add git worktree isolation to TAP so agents can work on isolated copies of a
repo without file conflicts. Modeled after Claude Code's `isolation: "worktree"`
parameter. Each isolated agent gets its own worktree branch; cleanup is
automatic on kill/completion.

## Success Criteria
- `tap spawn --name X --isolation worktree` creates a git worktree for the agent
- Agent subprocess runs with `cwd` set to the worktree directory
- `tap kill X` removes the worktree automatically
- File lock (`fcntl.flock`) prevents concurrent `git worktree add/remove` races
- Non-isolated spawn path unchanged (backward compatible)
- All existing tests still pass
- New tests cover: create, cwd isolation, cleanup on kill, lock contention

## Tasks

### Task 1: Add worktree constants and isolation mode
- **Test first**: `python3 -c "from tap.constants import ISOLATION_WORKTREE, WORKTREE_BASE_DIR; assert ISOLATION_WORKTREE == 'worktree'"`
- **Implementation**: Add to `tap/constants.py`:
  - `ISOLATION_NONE = "none"`
  - `ISOLATION_WORKTREE = "worktree"`
  - `WORKTREE_BASE_DIR = os.environ.get("TAP_WORKTREE_DIR", "/tmp/tap-worktrees")`
- **Verify**: `python3 -c "from tap.constants import ISOLATION_WORKTREE, WORKTREE_BASE_DIR"`
- **Depends on**: none

### Task 2: Build WorktreeManager — create/remove/list
- **Test first**: `tap/tests/test_worktree.py` — test create returns valid path with `.git` file, remove deletes it, list tracks active worktrees, concurrent create uses flock
- **Implementation**: New file `tap/worktree.py` (~120 lines):
  - `WorktreeManager` class with `_lock_path` using `fcntl.flock`
  - `create(agent_id: str, repo_dir: str) -> str` — runs `git worktree add`, returns worktree path
  - `remove(agent_id: str) -> bool` — runs `git worktree remove`, returns success
  - `list() -> dict[str, str]` — maps agent_id to worktree path
  - `cleanup_all()` — remove all TAP worktrees (for daemon shutdown)
- **Verify**: `python3 -m pytest tap/tests/test_worktree.py -v`
- **Depends on**: Task 1

### Task 3: Add isolation field to ManagedAgent and SpawnRequest
- **Test first**: Assert `ManagedAgent` accepts `isolation="worktree"` and `worktree_path` fields. Assert `SpawnRequest` accepts `isolation` field defaulting to `"none"`.
- **Implementation**:
  - `tap/agent_manager.py` — add `isolation: str = ISOLATION_NONE` and `worktree_path: str | None = None` to `ManagedAgent` dataclass
  - `tap/protocol.py` — add `isolation: str = "none"` to `SpawnRequest`, add `worktree_path: str | None = None` to `SpawnResponse`
- **Verify**: `python3 -m pytest tap/tests/test_agent_manager.py -v`
- **Depends on**: Task 1

### Task 4: Wire worktree into AgentManager.spawn() and kill()
- **Test first**: Spawn with `isolation="worktree"` → mock verifies `subprocess.Popen` called with `cwd=<worktree_path>`. Kill → verify `WorktreeManager.remove()` called.
- **Implementation**: In `tap/agent_manager.py`:
  - `__init__`: accept optional `repo_dir` param, create `WorktreeManager` instance
  - `spawn()`: accept `isolation` param. If `"worktree"`, call `worktree_mgr.create()`, pass `cwd=worktree_path` to `Popen`
  - `kill()`: if agent has `worktree_path`, call `worktree_mgr.remove()` after process termination
  - `_cleanup()`: same worktree removal
- **Verify**: `python3 -m pytest tap/tests/test_agent_manager.py tap/tests/test_worktree.py -v`
- **Depends on**: Task 2, Task 3

### Task 5: Wire isolation into daemon RPC and CLI
- **Test first**: Test daemon `session/spawn` RPC accepts `isolation` field and passes to agent manager. Test CLI `--isolation worktree` flag.
- **Implementation**:
  - `tap/daemon.py` — `_handle_spawn()` reads `isolation` from request, passes to `manager.spawn()`
  - `tap/cli.py` — add `--isolation` flag to `spawn` subcommand (choices: none, worktree)
  - `tap/client.py` — `spawn()` method accepts `isolation` param, includes in RPC payload
- **Verify**: `python3 -m pytest tap/tests/test_daemon.py -v`
- **Depends on**: Task 4

### Task 6: Integration test — full lifecycle with worktree
- **Test first**: End-to-end in `tap/tests/test_worktree.py`:
  1. Init a temp git repo
  2. Spawn agent with `isolation="worktree"` using MockAdapter
  3. Verify agent process cwd is the worktree (not the original repo)
  4. Verify worktree branch exists in `git worktree list`
  5. Kill agent
  6. Verify worktree directory is gone
  7. Verify `git worktree list` no longer shows it
- **Implementation**: Test-only — no production code changes
- **Verify**: `python3 -m pytest tap/tests/test_worktree.py -v`
- **Depends on**: Task 5

## Verification (end-to-end)
```bash
python3 -m pytest tap/tests/ -v
```

## Rollback
All changes are additive — new file `worktree.py`, new fields with defaults,
new test file. To rollback: delete `tap/worktree.py`, `tap/tests/test_worktree.py`,
revert field additions. No existing behavior changes.
