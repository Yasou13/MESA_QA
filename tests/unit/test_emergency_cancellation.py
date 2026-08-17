from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.state_machine import State
from mesa_qa.codex.runner import CodexRunner


@pytest.mark.asyncio
async def test_stop_cancels_active_action_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 1.0

    controller = QAController(cfg, run_id="run-cancel-test")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.scenario_engine.load_suite = lambda: None
    ev = ScenarioEvent(id="ev-long", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    controller.scenario_engine.next_event = lambda: ev
    controller.scenario_engine.has_next = lambda: True
    controller.process_mgr.stop_all = AsyncMock()

    # Simulate an active long-running action
    action_started = asyncio.Event()
    action_cancelled = asyncio.Event()

    async def _mock_process_event(event):
        action_started.set()
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            action_cancelled.set()
            raise

    controller._process_event = _mock_process_event

    loop_task = asyncio.create_task(controller.run_loop())

    await action_started.wait()
    assert controller._current_action_task is not None

    # Call stop()
    await controller.stop()
    await loop_task

    assert action_cancelled.is_set()
    assert controller.state_machine.current == State.COMPLETED


@pytest.mark.asyncio
async def test_db_control_stop_cancels_active_action_promptly(tmp_path, monkeypatch):
    """Test that writing stop to SQLite control_requests cancels an active action in real-time."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 1.0

    controller = QAController(cfg, run_id="run-db-stop-test")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.scenario_engine.load_suite = lambda: None
    ev = ScenarioEvent(id="ev-long-db", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    controller.scenario_engine.next_event = lambda: ev
    controller.scenario_engine.has_next = lambda: True
    controller.process_mgr.stop_all = AsyncMock()

    action_started = asyncio.Event()
    action_cancelled = asyncio.Event()

    async def _mock_process_event(event):
        action_started.set()
        try:
            await asyncio.sleep(30.0)  # 30 second natural timeout
        except asyncio.CancelledError:
            action_cancelled.set()
            raise

    controller._process_event = _mock_process_event

    t0 = time.time()
    loop_task = asyncio.create_task(controller.run_loop())

    await action_started.wait()
    assert controller._current_action_task is not None

    # Write 'stop' into control DB (simulating `mesa-qa stop --run-id run-db-stop-test`)
    await controller.controller_db.request_control("run-db-stop-test", "stop")

    # Await loop completion
    await asyncio.wait_for(loop_task, timeout=5.0)
    elapsed = time.time() - t0

    assert action_cancelled.is_set()
    assert elapsed < 5.0, f"Expected emergency stop in <5s, took {elapsed}s"
    assert controller.state_machine.current == State.COMPLETED

    records = controller.evidence_store.read_json_records("emergency_stop.json")
    assert len(records) >= 1
    assert records[0]["action"] == "stop"
    assert records[0]["active_action_cancelled"] is True


@pytest.mark.asyncio
async def test_codex_runner_kills_child_process_on_cancellation(tmp_path):
    mock_bin = tmp_path / "mock_codex.sh"
    mock_bin.write_text("#!/bin/sh\nsleep 30\n")
    mock_bin.chmod(0o755)

    runner = CodexRunner(codex_binary=str(mock_bin))

    task = asyncio.create_task(runner.run(prompt="test", cwd=tmp_path, timeout_seconds=10))

    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
