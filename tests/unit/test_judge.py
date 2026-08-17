import pytest
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.judge.deterministic import DeterministicJudge

@pytest.mark.asyncio
async def test_deterministic_judge_pass():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(action_id="act1", scenario_event_id="ev1", actual={"answer": "Project Atlas uses FastAPI framework"})
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is True
    assert verdict.is_candidate_anomaly is False

@pytest.mark.asyncio
async def test_deterministic_judge_mismatch():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(action_id="act1", scenario_event_id="ev1", actual={"answer": "Project Atlas uses Django"})
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.is_candidate_anomaly is True


@pytest.mark.asyncio
async def test_negated_recall_must_not_pass():
    # FastAPI vs 'does not use FastAPI' must NOT PASS
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(
        action_id="act1",
        scenario_event_id="ev1",
        actual={"answer": "Atlas does not use FastAPI."},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.category == "NEEDS_REVIEW"
    assert "negated" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_normalized_true_positive_pass():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(
        action_id="act1",
        scenario_event_id="ev1",
        actual={"answer": "The backend framework is FASTAPI!"},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is True
    assert verdict.is_candidate_anomaly is False


@pytest.mark.asyncio
async def test_ambiguous_response_not_pass():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(
        action_id="act1",
        scenario_event_id="ev1",
        actual={"answer": "It is unclear whether Atlas uses FastAPI or Flask."},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.category == "NEEDS_REVIEW"
    assert "ambiguous" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_contrastive_clause_with_other_entity_negated_passes():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(
        action_id="act1",
        scenario_event_id="ev1",
        actual={"answer": "Atlas does not use Django, but it uses FastAPI."},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is True
    assert verdict.is_candidate_anomaly is False


@pytest.mark.asyncio
async def test_structured_result_prioritized():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(
        action_id="act1",
        scenario_event_id="ev1",
        actual={"answer": "Unstructured text", "value": "FastAPI"},
    )
    verdict = await judge.judge(ev, obs, None)
    assert verdict.is_pass is True

