from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_confirmed_bug_continuation_when_repair_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = False  # explicitly disabled

    controller = QAController(cfg, run_id="run-bug-cont")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    recorded_states: list[State] = []
    controller.state_machine._on_change = lambda old, new: recorded_states.append(new)

    # Mock tester to return an anomalous observation that reproduces on recheck
    async def mock_execute(event, action_id, *args, **kwargs):
        return TesterObservation(
            action_id=action_id,
            scenario_event_id=event.id,
            tools_called=["mesa_recall"],
            actual={"answer": "Project Atlas uses wrong backend."},
            tester_assessment="pass",
        )

    controller.tester.execute_action = AsyncMock(side_effect=mock_execute)

    ev = ScenarioEvent(
        id="ev-fail", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI"
    )

    # Process event
    await controller._process_event(ev)

    # Verify bug was recorded
    assert len(controller._bugs) == 1
    assert controller._bugs[0]["bug_id"] == "BUG-0001"

    # Verify evidence bundle created
    bundle_path = controller.evidence_store.evidence_dir / "BUG-0001" / "bug.json"
    assert bundle_path.exists()

    # Verify state machine returned to RUNNING
    assert controller.state_machine.current == State.RUNNING
    assert State.ANOMALY in recorded_states
    assert State.RECHECKING in recorded_states
    assert State.REPRODUCING in recorded_states
    assert State.CONFIRMED_BUG in recorded_states
    assert recorded_states[-1] == State.RUNNING


@pytest.mark.asyncio
async def test_repair_pipeline_invoked_when_repair_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True

    controller = QAController(cfg, run_id="run-bug-repair")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    recorded_states: list[State] = []
    controller.state_machine._on_change = lambda old, new: recorded_states.append(new)

    async def mock_execute(event, action_id, *args, **kwargs):
        return TesterObservation(
            action_id=action_id,
            scenario_event_id=event.id,
            tools_called=["mesa_recall"],
            actual={"answer": "wrong backend"},
            tester_assessment="pass",
        )

    controller.tester.execute_action = AsyncMock(side_effect=mock_execute)

    ev = ScenarioEvent(
        id="ev-fail", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI"
    )

    await controller._process_event(ev)

    assert len(controller._bugs) == 1
    assert State.CONFIRMED_BUG in recorded_states
    assert State.REPAIRING in recorded_states
