from __future__ import annotations

import pytest

from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator


@pytest.mark.asyncio
async def test_historical_facts_exclusion_of_current(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()

    # Step 1: Initial remember
    await oracle_db.apply_event(
        ScenarioEvent(
            id="ev-1",
            kind=ActionKind.REMEMBER,
            entity="project:atlas",
            field="backend",
            value="FastAPI",
        )
    )
    assert await oracle_db.get_current_fact("project:atlas", "backend") == "FastAPI"
    assert await oracle_db.get_historical_facts("project:atlas", "backend") == []

    # Step 2: First correction
    await oracle_db.apply_event(
        ScenarioEvent(
            id="ev-2",
            kind=ActionKind.CORRECT,
            entity="project:atlas",
            field="backend",
            old_value="FastAPI",
            value="Spring Boot",
        )
    )
    assert await oracle_db.get_current_fact("project:atlas", "backend") == "Spring Boot"
    hist1 = await oracle_db.get_historical_facts("project:atlas", "backend")
    assert hist1 == ["FastAPI"]
    assert "Spring Boot" not in hist1

    # Step 3: Second correction
    await oracle_db.apply_event(
        ScenarioEvent(
            id="ev-3",
            kind=ActionKind.CORRECT,
            entity="project:atlas",
            field="backend",
            old_value="Spring Boot",
            value="Go Gin",
        )
    )
    assert await oracle_db.get_current_fact("project:atlas", "backend") == "Go Gin"
    hist2 = await oracle_db.get_historical_facts("project:atlas", "backend")
    assert hist2 == ["FastAPI", "Spring Boot"]
    assert "Go Gin" not in hist2


@pytest.mark.asyncio
async def test_historical_recall_evaluator(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()
    evaluator = OracleEvaluator(oracle_db)

    await oracle_db.apply_event(
        ScenarioEvent(
            id="e1",
            kind=ActionKind.REMEMBER,
            entity="app:alpha",
            field="db",
            value="MySQL",
        )
    )
    await oracle_db.apply_event(
        ScenarioEvent(
            id="e2",
            kind=ActionKind.CORRECT,
            entity="app:alpha",
            field="db",
            old_value="MySQL",
            value="PostgreSQL",
        )
    )

    # Historical query with expected=None (evaluator queries DB automatically)
    hist_recall = ScenarioEvent(
        id="e3",
        kind=ActionKind.RECALL,
        entity="app:alpha",
        field="db",
        mode="historical",
        expected=None,
    )
    obs = TesterObservation(
        action_id="act_001",
        scenario_event_id="e3",
        actual={"answer": "Previously alpha used MySQL before migrating."},
        tester_assessment="pass",
    )
    verdict = await evaluator.evaluate_observation(hist_recall, obs)
    assert verdict.is_pass
    assert not verdict.is_candidate_anomaly
