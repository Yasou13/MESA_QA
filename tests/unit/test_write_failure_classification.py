from __future__ import annotations

import pytest
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.judge.deterministic import DeterministicJudge
from mesa_qa.oracle.evaluator import OracleEvaluator
from mesa_qa.oracle.db import OracleDB


@pytest.mark.asyncio
async def test_write_success_committed():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev_w1", kind=ActionKind.REMEMBER, entity="user:1", field="name", expected="Alice")
    obs = TesterObservation(
        action_id="act_w1",
        scenario_event_id="ev_w1",
        actual={"operation_id": "op_0123456789abcdef0123456789abcdef", "operation_state": "COMMITTED"},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is True
    assert verdict.is_candidate_anomaly is False


@pytest.mark.asyncio
async def test_write_policy_rejection():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev_w2", kind=ActionKind.REMEMBER, entity="user:1", field="role", expected="ADMIN")
    obs = TesterObservation(
        action_id="act_w2",
        scenario_event_id="ev_w2",
        actual={
            "operation_id": "op_0123456789abcdef0123456789abcdef",
            "operation_state": "REJECTED",
            "error": {"code": "POLICY_DENIED", "message": "Role assignment forbidden by RBAC policy"},
        },
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.is_candidate_anomaly is False
    assert verdict.category == "EXPECTED_POLICY"
    assert "policy" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_write_infrastructure_provider_error():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev_w3", kind=ActionKind.REMEMBER, entity="user:1", field="city", expected="London")
    obs = TesterObservation(
        action_id="act_w3",
        scenario_event_id="ev_w3",
        actual={
            "operation_id": "op_0123456789abcdef0123456789abcdef",
            "operation_state": "FAILED",
            "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Embedding provider rate limit exceeded"},
        },
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.is_candidate_anomaly is False
    assert verdict.category == "INFRASTRUCTURE"


@pytest.mark.asyncio
async def test_write_candidate_anomaly_internal_failure(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()
    evaluator = OracleEvaluator(oracle_db)

    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev_w4", kind=ActionKind.REMEMBER, entity="user:1", field="bio", expected="Developer")
    obs = TesterObservation(
        action_id="act_w4",
        scenario_event_id="ev_w4",
        actual={
            "operation_id": "op_0123456789abcdef0123456789abcdef",
            "operation_state": "FAILED",
            "error": {"code": "INTERNAL_ERROR", "message": "Database storage corruption or state machine crash"},
        },
    )
    verdict = await judge.judge(ev, obs, evaluator)
    assert verdict.is_pass is False
    assert verdict.is_candidate_anomaly is True
    assert verdict.category == "CANDIDATE_ANOMALY"
