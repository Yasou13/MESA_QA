from __future__ import annotations

import pytest

from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator


@pytest.mark.asyncio
async def test_forget_to_re_remember_lifecycle(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()

    # 1. Initial REMEMBER
    ev1 = ScenarioEvent(
        id="ev-1",
        kind=ActionKind.REMEMBER,
        entity="user:alice",
        field="email",
        value="alice@example.com",
    )
    await oracle_db.apply_event(ev1)

    assert not await oracle_db.is_forgotten("user:alice", "email")
    assert await oracle_db.get_current_fact("user:alice", "email") == "alice@example.com"

    # 2. FORGET
    ev2 = ScenarioEvent(
        id="ev-2",
        kind=ActionKind.FORGET,
        entity="user:alice",
        field="email",
    )
    await oracle_db.apply_event(ev2)

    assert await oracle_db.is_forgotten("user:alice", "email")
    assert await oracle_db.get_current_fact("user:alice", "email") is None

    # 3. RE-REMEMBER
    ev3 = ScenarioEvent(
        id="ev-3",
        kind=ActionKind.REMEMBER,
        entity="user:alice",
        field="email",
        value="alice_new@example.com",
    )
    await oracle_db.apply_event(ev3)

    # Must NOT be considered forgotten anymore!
    assert not await oracle_db.is_forgotten("user:alice", "email")
    assert await oracle_db.get_current_fact("user:alice", "email") == "alice_new@example.com"


@pytest.mark.asyncio
async def test_re_remember_evaluator_behavior(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()
    evaluator = OracleEvaluator(oracle_db)

    # Lifecycle: remember -> forget -> re-remember
    await oracle_db.apply_event(
        ScenarioEvent(id="e1", kind=ActionKind.REMEMBER, entity="server:prod", field="ip", value="10.0.0.1")
    )
    await oracle_db.apply_event(
        ScenarioEvent(id="e2", kind=ActionKind.FORGET, entity="server:prod", field="ip")
    )
    await oracle_db.apply_event(
        ScenarioEvent(id="e3", kind=ActionKind.REMEMBER, entity="server:prod", field="ip", value="10.0.0.2")
    )

    # Current recall should succeed on the new IP
    recall_ev = ScenarioEvent(
        id="e4",
        kind=ActionKind.RECALL,
        entity="server:prod",
        field="ip",
        mode="current",
    )
    obs = TesterObservation(
        action_id="act_001",
        scenario_event_id="e4",
        actual={"answer": "The server ip is 10.0.0.2"},
        tester_assessment="pass",
    )
    verdict = await evaluator.evaluate_observation(recall_ev, obs)
    assert verdict.is_pass
    assert not verdict.is_candidate_anomaly
