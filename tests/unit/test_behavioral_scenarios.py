from __future__ import annotations

from pathlib import Path
import pytest

from mesa_qa.models import ActionKind
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator
from mesa_qa.scenario.engine import ScenarioEngine


def test_scenario_suite_coverage():
    scenarios_dir = Path(__file__).parent.parent.parent / "scenarios"
    engine = ScenarioEngine(scenarios_dir=scenarios_dir, seed=42)
    engine.load_suite()

    all_kinds = {event.kind for event in engine.events}

    required_kinds = {
        ActionKind.REMEMBER,
        ActionKind.RECALL,
        ActionKind.CORRECT,
        ActionKind.FORGET,
        ActionKind.DUPLICATE,
        ActionKind.SEMANTIC_DUPLICATE,
        ActionKind.IDEMPOTENCY,
        ActionKind.CONFLICT,
        ActionKind.MULTI_FACT,
    }

    assert required_kinds.issubset(all_kinds), f"Missing kinds: {required_kinds - all_kinds}"


@pytest.mark.asyncio
async def test_all_behavioral_scenarios_execute_in_oracle(tmp_path):
    scenarios_dir = Path(__file__).parent.parent.parent / "scenarios"
    engine = ScenarioEngine(scenarios_dir=scenarios_dir, seed=42)
    engine.load_suite()

    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()
    evaluator = OracleEvaluator(oracle_db)

    for event in engine.events:
        if event.kind in {ActionKind.ROTATE_SESSION, ActionKind.RESTART_RUNTIME}:
            continue
        await oracle_db.apply_event(event)

    # Verify facts were populated for multi-fact
    assert await oracle_db.get_current_fact("db:primary", "engine") == "PostgreSQL"
    assert await oracle_db.get_current_fact("db:primary", "replicas") == 3

    # Verify resolved conflict
    assert await oracle_db.get_current_fact("service:auth", "port") == 8443

    # Verify duplicate & idempotency
    assert await oracle_db.get_current_fact("cluster:prod-us", "region") == "us-east-1"

    # Verify re-remembered fact
    assert await oracle_db.get_current_fact("user:charlie", "title") == "Staff Engineer"
    assert not await oracle_db.is_forgotten("user:charlie", "title")
