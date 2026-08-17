from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_initialize_exception_cleans_up_and_persists_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    run_id = "run-init-fail"

    controller = QAController(cfg, run_id=run_id)
    controller.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "main-head", "is_clean": True, "branch": "main"}
    controller.process_mgr.worktree_mgr.capture_main_baseline = lambda: {"head": "main-head", "status": ""}

    stop_all_mock = AsyncMock()
    controller.process_mgr.stop_all = stop_all_mock
    assert_main_mock = MagicMock()
    controller.process_mgr.worktree_mgr.assert_main_unchanged = assert_main_mock

    # Cause failure during candidate setup
    controller.process_mgr.setup_worktree = MagicMock(side_effect=RuntimeError("Disk full error"))

    with pytest.raises(RuntimeError, match="Disk full error"):
        await controller.initialize()

    assert controller.state_machine.current == State.FAILED
    stop_all_mock.assert_awaited_once()
    assert_main_mock.assert_called_once()

    persisted = await controller.controller_db.get_run_state(run_id)
    assert persisted is not None
    assert persisted["status"] == "FAILED"


@pytest.mark.asyncio
async def test_run_loop_exception_cleans_up_and_persists_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 1.0
    run_id = "run-loop-fail"

    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING
    controller._main_baseline = {"head": "main-head", "status": ""}

    stop_all_mock = AsyncMock()
    controller.process_mgr.stop_all = stop_all_mock
    assert_main_mock = MagicMock()
    controller.process_mgr.worktree_mgr.assert_main_unchanged = assert_main_mock

    ev = ScenarioEvent(id="ev-fail", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    controller.scenario_engine.next_event = lambda: ev
    controller.scenario_engine.has_next = lambda: True

    # Cause crash in _process_event
    controller._process_event = AsyncMock(side_effect=RuntimeError("Unexpected unhandled exception"))

    with pytest.raises(RuntimeError, match="Unexpected unhandled exception"):
        await controller.run_loop()

    assert controller.state_machine.current == State.FAILED
    stop_all_mock.assert_awaited_once()
    assert_main_mock.assert_called_once()

    persisted = await controller.controller_db.get_run_state(run_id)
    assert persisted is not None
    assert persisted["status"] == "FAILED"
