from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_critical_states_persisted_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-persist-crit")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    # Test WAITING_FOR_CODEX from RUNNING
    await controller._set_state(State.WAITING_FOR_CODEX)
    state = await controller.controller_db.get_run_state("run-persist-crit")
    assert state is not None
    assert state["status"] == "WAITING_FOR_CODEX"

    # Test RUNNING from WAITING_FOR_CODEX
    await controller._set_state(State.RUNNING)
    state = await controller.controller_db.get_run_state("run-persist-crit")
    assert state is not None
    assert state["status"] == "RUNNING"

    # Test PAUSED from RUNNING
    await controller._set_state(State.PAUSED)
    state = await controller.controller_db.get_run_state("run-persist-crit")
    assert state is not None
    assert state["status"] == "PAUSED"

    # Test STOPPING from PAUSED
    await controller._set_state(State.STOPPING)
    state = await controller.controller_db.get_run_state("run-persist-crit")
    assert state is not None
    assert state["status"] == "STOPPING"

    # Test COMPLETED from STOPPING
    await controller._set_state(State.COMPLETED)
    state = await controller.controller_db.get_run_state("run-persist-crit")
    assert state is not None
    assert state["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_failed_state_persisted_on_unhandled_fatal_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-persist-fail")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()

    # If run_loop or initialize encounters unhandled failure
    await controller._set_state(State.FAILED)
    state = await controller.controller_db.get_run_state("run-persist-fail")
    assert state is not None
    assert state["status"] == "FAILED"


@pytest.mark.asyncio
async def test_infra_error_persists_waiting_for_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-persist-infra")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.tester.execute_action = AsyncMock(
        return_value=TesterObservation(
            action_id="act-infra",
            scenario_event_id="ev-infra",
            tools_called=[],
            actual={},
            tester_assessment="infra_error",
            reason="Codex failed",
        )
    )

    ev = ScenarioEvent(id="ev-infra", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    await controller._process_event(ev)

    state = await controller.controller_db.get_run_state("run-persist-infra")
    assert state is not None
    assert state["status"] == "WAITING_FOR_CODEX"
