from __future__ import annotations

from typing import Any

from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict


class DeterministicJudge:
    async def judge(self, event: ScenarioEvent, observation: TesterObservation, oracle_evaluator: Any) -> Verdict:
        if oracle_evaluator is not None:
            return await oracle_evaluator.evaluate_observation(event, observation)
        if observation.tester_assessment == "infra_error":
            return Verdict(is_pass=False, is_candidate_anomaly=False, category="INFRASTRUCTURE", reason=observation.reason or "MCP/HTTP infrastructure error")
        if event.kind.value != "recall":
            state = str(observation.actual.get("operation_state", "")).upper()
            if observation.actual.get("operation_id") and state in {"COMPLETED", "SUCCEEDED", "SUCCESS"}:
                return Verdict(is_pass=True, is_candidate_anomaly=False)
            return Verdict(is_pass=False, is_candidate_anomaly=False, category="OPERATION_FINALITY", reason="Write operation is not terminal")
        actual = str(observation.actual.get("answer") or observation.actual.get("raw_response", ""))
        if event.expected is not None and str(event.expected).lower() in actual.lower():
            return Verdict(is_pass=True, is_candidate_anomaly=False, expected=event.expected, actual=actual)
        return Verdict(is_pass=False, is_candidate_anomaly=True, category="RETRIEVAL_MISMATCH", reason="Expected truth was not found", expected=event.expected, actual=actual)
