from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation, Verdict
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_reproduction_idempotency_strategy_fresh_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    controller = QAController(cfg, run_id="run-repro-fresh")
    await controller.controller_db.initialize()

    event = ScenarioEvent(
        id="ev-write-01",
        kind=ActionKind.REMEMBER,
        entity="server:prod",
        field="ip",
        value="10.0.0.1",
        idempotency_key="qa:run-repro-fresh:act_000001:1",
    )
    obs = TesterObservation(
        action_id="act_000001",
        scenario_event_id="ev-write-01",
        actual={"operation_id": "op_01", "operation_state": "FAILED"},
        tester_assessment="pass",
    )
    anomaly_verdict = Verdict(
        is_pass=False,
        is_candidate_anomaly=True,
        category="OPERATION_FINALITY",
        reason="Write operation failed",
    )

    recheck_event_captured = None

    async def mock_execute(ev, action_id, ws):
        nonlocal recheck_event_captured
        recheck_event_captured = ev
        return TesterObservation(
            action_id=action_id,
            scenario_event_id=ev.id,
            actual={"operation_id": "op_recheck", "operation_state": "FAILED"},
            tester_assessment="pass",
        )

    controller.tester.execute_action = mock_execute
    controller.judge.judge = AsyncMock(return_value=anomaly_verdict)
    controller.state_machine._current_state = State.RUNNING

    await controller._handle_anomaly(event, obs, anomaly_verdict)

    assert recheck_event_captured is not None
    # For fresh_attempt, the idempotency_key must be fresh (different from initial)
    assert recheck_event_captured.idempotency_key != event.idempotency_key
    assert "recheck_act_000001:2" in recheck_event_captured.idempotency_key

    # Check bug report
    assert len(controller._bugs) == 1
    bug = controller._bugs[0]
    assert bug["reproduction_strategy"] == "fresh_attempt"
    assert bug["preconditions"]["reproduction_strategy"] == "fresh_attempt"


@pytest.mark.asyncio
async def test_reproduction_idempotency_strategy_reuse_same_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    controller = QAController(cfg, run_id="run-repro-reuse")
    await controller.controller_db.initialize()

    event = ScenarioEvent(
        id="ev-idem-01",
        kind=ActionKind.IDEMPOTENCY,
        entity="server:prod",
        field="ip",
        value="10.0.0.1",
        idempotency_key="key_custom_12345",
        idempotency_strategy="reuse_same_key",
    )
    obs = TesterObservation(
        action_id="act_000002",
        scenario_event_id="ev-idem-01",
        actual={"operation_id": "op_02", "operation_state": "FAILED"},
        tester_assessment="pass",
    )
    anomaly_verdict = Verdict(
        is_pass=False,
        is_candidate_anomaly=True,
        category="OPERATION_FINALITY",
        reason="Idempotency failed",
    )

    recheck_event_captured = None

    async def mock_execute(ev, action_id, ws):
        nonlocal recheck_event_captured
        recheck_event_captured = ev
        return TesterObservation(
            action_id=action_id,
            scenario_event_id=ev.id,
            actual={"operation_id": "op_recheck", "operation_state": "FAILED"},
            tester_assessment="pass",
        )

    controller.tester.execute_action = mock_execute
    controller.judge.judge = AsyncMock(return_value=anomaly_verdict)
    controller.state_machine._current_state = State.RUNNING

    await controller._handle_anomaly(event, obs, anomaly_verdict)

    assert recheck_event_captured is not None
    # For reuse_same_key, idempotency_key must be preserved
    assert recheck_event_captured.idempotency_key == "key_custom_12345"

    assert len(controller._bugs) == 1
    bug = controller._bugs[0]
    assert bug["reproduction_strategy"] == "reuse_same_key"
