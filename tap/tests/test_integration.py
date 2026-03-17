"""Integration tests — full spawn/dispatch/result cycle."""

import os
import time

import pytest

from tap.adapters.mock import MockAdapter
from tap.client import TAPClient
from tap.daemon import TAPDaemon
from tap.storage import TAPStorage


@pytest.fixture()
def system(tmp_path):
    """Full TAP system: daemon + client."""
    sock = str(tmp_path / "tap-integ.sock")
    os.environ["TAP_DB_PATH"] = str(tmp_path / "integ.db")

    daemon = TAPDaemon(
        socket_path=sock,
        adapter_factory=MockAdapter,
        storage=TAPStorage(),
    )
    daemon.start_background()
    time.sleep(0.3)
    client = TAPClient(sock)
    yield daemon, client
    daemon.stop()
    os.environ.pop("TAP_DB_PATH", None)


def test_full_cycle(system):
    """Spawn agent, dispatch task, check status, kill agent."""
    daemon, client = system

    resp = client.spawn("integ-1", role="tester")
    assert resp["status"] == "ready"

    resp = client.send("integ-1", "echo hello", priority=3)
    assert resp["accepted"]
    task_id = resp["task_id"]

    status = client.status("integ-1")
    assert status["alive"] is True
    assert status["state"] in ("ready", "working")

    task = client.task_status(task_id)
    assert task["task_id"] == task_id

    resp = client.kill("integ-1")
    assert resp["killed"] is True

    status = client.status("integ-1")
    assert status["alive"] is False


def test_persistent_agent_multiple_tasks(system):
    """Persistent agent handles two tasks without respawn."""
    daemon, client = system

    client.spawn("p1", role="tester", persistent=True)

    resp1 = client.send("p1", "task one")
    assert resp1["accepted"]

    time.sleep(0.5)

    resp2 = client.send("p1", "task two")
    assert resp2["accepted"]

    status = client.status("p1")
    assert status["alive"] is True

    client.kill("p1")


def test_ephemeral_agent(system):
    """Ephemeral agent works for a task."""
    daemon, client = system

    client.spawn("e1", role="tester", persistent=False)
    resp = client.send("e1", "quick task")
    assert resp["accepted"]

    status = client.status("e1")
    assert status["alive"] is True

    client.kill("e1")


def test_multiple_agents(system):
    """Multiple agents can run concurrently."""
    daemon, client = system

    client.spawn("a1", role="researcher")
    client.spawn("a2", role="builder")
    client.spawn("a3", role="reviewer")

    listing = client.status()
    assert len(listing["agents"]) == 3

    client.send("a1", "research task")
    client.send("a2", "build task")
    client.send("a3", "review task")

    for aid in ("a1", "a2", "a3"):
        s = client.status(aid)
        assert s["alive"] is True

    for aid in ("a1", "a2", "a3"):
        client.kill(aid)


def test_handoff_between_agents(system):
    """Handoff creates a new task for the receiving agent."""
    daemon, client = system

    client.spawn("from-agent", role="researcher")
    client.spawn("to-agent", role="builder")

    resp = client.send("from-agent", "initial research")
    task_id = resp["task_id"]

    handoff = client.handoff(
        from_agent="from-agent",
        to_agent="to-agent",
        task_id=task_id,
        summary="Found 3 options, recommend option B",
    )
    assert handoff["from_agent"] == "from-agent"
    assert handoff["to_agent"] == "to-agent"
    assert handoff["task_id"] is not None

    client.kill("from-agent")
    client.kill("to-agent")


def test_broadcast_received(system):
    """Broadcast is stored and can be read."""
    daemon, client = system

    client.spawn("listener", role="tester")
    resp = client.broadcast("attention all agents", from_agent="orchestrator")
    assert resp["sent"] is True
    client.kill("listener")
