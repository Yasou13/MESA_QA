from __future__ import annotations

import json
from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_thread_rotation_gate_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-rot-pass")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()

    # Precondition: Remember fact in oracle
    remember_ev = ScenarioEvent(
        id="ev-rem", kind=ActionKind.REMEMBER, entity="project:atlas", field="backend", value="FastAPI", text="FastAPI backend"
    )
    await controller.oracle_db.apply_event(remember_ev)

    # 1. Tester was on thread-1
    controller.tester.thread_id = "thread-1"

    # 2. Rotate session event
    rot_ev = ScenarioEvent(id="ev-rot", kind=ActionKind.ROTATE_SESSION, entity="project:atlas")
    await controller._process_event(rot_ev)
    assert controller.tester.thread_id is None
    assert controller._rotation_pending_old_thread == "thread-1"

    # 3. Next recall event executes on fresh thread-2
    async def mock_execute(*args, **kwargs):
        controller.tester.thread_id = "thread-2"
        return TesterObservation(
            action_id="act-recall",
            scenario_event_id="ev-recall",
            tools_called=["mesa_recall"],
            actual={"answer": "Project Atlas uses FastAPI framework"},
            tester_assessment="pass",
        )

    controller.tester.execute_action = AsyncMock(side_effect=mock_execute)

    recall_ev = ScenarioEvent(
        id="ev-recall", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI"
    )
    await controller._process_event(recall_ev)

    # Verify evidence record
    rot_records = controller.evidence_store.read_json_records("thread_rotation.json")
    assert len(rot_records) == 1
    rec = rot_records[0]
    assert rec["status"] == "PASS"
    assert rec["old_thread_id"] == "thread-1"
    assert rec["new_thread_id"] == "thread-2"
    assert rec["recall_verdict"] == "PASS"
    assert rec["mcp_tool_verified"] is True


@pytest.mark.asyncio
async def test_thread_rotation_gate_fails_if_same_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-rot-fail-same")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()

    controller.state_machine._current_state = State.RUNNING

    controller.tester.thread_id = "thread-1"
    rot_ev = ScenarioEvent(id="ev-rot", kind=ActionKind.ROTATE_SESSION, entity="project:atlas")
    await controller._process_event(rot_ev)

    # Next turn mistakenly reuses thread-1
    async def mock_execute(*args, **kwargs):
        controller.tester.thread_id = "thread-1"
        return TesterObservation(
            action_id="act-recall",
            scenario_event_id="ev-recall",
            tools_called=["mesa_recall"],
            actual={"answer": "Project Atlas uses FastAPI framework"},
            tester_assessment="pass",
        )

    controller.tester.execute_action = AsyncMock(side_effect=mock_execute)

    recall_ev = ScenarioEvent(
        id="ev-recall", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI"
    )
    await controller._process_event(recall_ev)

    rot_records = controller.evidence_store.read_json_records("thread_rotation.json")
    assert len(rot_records) == 1
    assert rot_records[0]["status"] == "FAIL"


@pytest.mark.asyncio
async def test_thread_rotation_gate_fails_if_recall_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-rot-fail-recall")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.tester.thread_id = "thread-1"
    rot_ev = ScenarioEvent(id="ev-rot", kind=ActionKind.ROTATE_SESSION, entity="project:atlas")
    await controller._process_event(rot_ev)

    # Fresh thread created, but recall fails (wrong answer)
    async def mock_execute(*args, **kwargs):
        controller.tester.thread_id = "thread-2"
        return TesterObservation(
            action_id="act-recall",
            scenario_event_id="ev-recall",
            tools_called=["mesa_recall"],
            actual={"answer": "Project Atlas does not use FastAPI."},
            tester_assessment="pass",
        )

    controller.tester.execute_action = AsyncMock(side_effect=mock_execute)

    recall_ev = ScenarioEvent(
        id="ev-recall", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI"
    )
    await controller._process_event(recall_ev)

    rot_records = controller.evidence_store.read_json_records("thread_rotation.json")
    assert len(rot_records) == 1
    assert rot_records[0]["status"] == "FAIL"
    assert rot_records[0]["recall_verdict"] == "FAIL"

