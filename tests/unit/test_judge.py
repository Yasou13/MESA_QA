from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.judge.deterministic import DeterministicJudge

def test_deterministic_judge_pass():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(action_id="act1", scenario_event_id="ev1", actual={"answer": "Project Atlas uses FastAPI framework"})
    verdict = judge.judge(ev, obs, None)
    assert verdict.is_pass is True
    assert verdict.is_candidate_anomaly is False

def test_deterministic_judge_mismatch():
    judge = DeterministicJudge()
    ev = ScenarioEvent(id="ev1", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    obs = TesterObservation(action_id="act1", scenario_event_id="ev1", actual={"answer": "Project Atlas uses Django"})
    verdict = judge.judge(ev, obs, None)
    assert verdict.is_pass is False
    assert verdict.is_candidate_anomaly is True
