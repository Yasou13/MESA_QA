import pytest
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator
from mesa_qa.models import TesterObservation

@pytest.mark.asyncio
async def test_oracle_db_transitions(tmp_path):
    db_path = tmp_path / "oracle_test.db"
    oracle = OracleDB(db_path)
    await oracle.initialize()

    # 1. Remember
    ev1 = ScenarioEvent(id="ev1", kind=ActionKind.REMEMBER, entity="project:atlas", field="backend", value="FastAPI", text="FastAPI backend")
    await oracle.apply_event(ev1)
    await oracle.apply_event(ev1)  # replay must not duplicate fact side effects
    val = await oracle.get_current_fact("project:atlas", "backend")
    assert val == "FastAPI"

    # 2. Correct
    ev2 = ScenarioEvent(id="ev2", kind=ActionKind.CORRECT, entity="project:atlas", field="backend", old_value="FastAPI", value="Spring Boot", text="Spring Boot backend")
    await oracle.apply_event(ev2)
    val2 = await oracle.get_current_fact("project:atlas", "backend")
    assert val2 == "Spring Boot"

    # History
    history = await oracle.get_fact_history("project:atlas", "backend")
    assert history == ["FastAPI", "Spring Boot"]

    # 3. Forget
    ev3 = ScenarioEvent(id="ev3", kind=ActionKind.FORGET, entity="project:atlas", field="backend", text="Forget backend")
    await oracle.apply_event(ev3)
    is_forgotten = await oracle.is_forgotten("project:atlas", "backend")
    assert is_forgotten is True

    verdict = await OracleEvaluator(oracle).evaluate_observation(
        ScenarioEvent(id="recall-forgotten", kind=ActionKind.RECALL, entity="project:atlas", field="backend", mode="forgotten"),
        TesterObservation(action_id="act", scenario_event_id="recall-forgotten", actual={"answer": "The backend was Spring Boot"}),
    )
    assert verdict.is_pass is False
    assert verdict.category == "FORGET_RESURRECTION"
