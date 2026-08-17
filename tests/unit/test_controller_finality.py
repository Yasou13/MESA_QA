from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.mesa.approval import ApprovalLifecycleResult
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


class FakeApproval:
    def __init__(self, result: ApprovalLifecycleResult):
        self.result = result

    async def approve_and_wait(self, _operation_id, _ownership):
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("final_status", "outcome", "expected_truth"),
    [
        ("COMMITTED", "PASS", "FastAPI"),
        ("FAILED", "FAIL", None),
        ("TIMEOUT", "FAIL", None),
    ],
)
async def test_ground_truth_advances_only_after_committed(
    tmp_path, monkeypatch, final_status, outcome, expected_truth
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-finality")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING
    controller._binding_context = {
        "client_id": "codex-qa-tester",
        "binding_id": "binding-qa",
        "principal_id": "local-qa-tester",
        "tenant_id": "default",
        "workspace_id": "default",
        "dataset_id": "default",
    }
    controller._approval = FakeApproval(
        ApprovalLifecycleResult(
            operation_id="op_" + "a" * 32,
            outcome=outcome,
            final_status=final_status,
            ownership_verified=True,
            approval_invoked=True,
            approval_reason="MESA-QA synthetic test run run-finality",
        )
    )
    controller.tester.execute_action = AsyncMock(
        return_value=TesterObservation(
            action_id="ignored",
            scenario_event_id="remember-atlas",
            tools_called=["mesa_remember"],
            actual={
                "operation_id": "op_" + "a" * 32,
                "operation_state": "PENDING_APPROVAL",
            },
        )
    )
    scenario_event = ScenarioEvent(
        id="remember-atlas",
        kind=ActionKind.REMEMBER,
        entity="project:atlas",
        field="backend",
        value="FastAPI",
        text="Atlas backend is FastAPI.",
    )

    await controller._process_event(scenario_event)

    assert (
        await controller.oracle_db.get_current_fact("project:atlas", "backend")
        == expected_truth
    )
