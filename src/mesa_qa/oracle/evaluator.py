from __future__ import annotations

from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict
from mesa_qa.oracle.db import OracleDB


_WRITE_KINDS = {
    "remember", "correct", "forget", "duplicate", "semantic_duplicate",
    "multi_fact", "conflict", "idempotency",
}
_SUCCESS_STATES = {"COMPLETED", "SUCCEEDED", "SUCCESS"}


class OracleEvaluator:
    def __init__(self, oracle_db: OracleDB):
        self.oracle_db = oracle_db

    async def evaluate_observation(
        self, event: ScenarioEvent, observation: TesterObservation
    ) -> Verdict:
        if observation.tester_assessment == "infra_error":
            return Verdict(is_pass=False, is_candidate_anomaly=False, category="INFRASTRUCTURE", reason=observation.reason or "MCP/HTTP infrastructure error")

        if event.kind.value in _WRITE_KINDS:
            state = str(observation.actual.get("operation_state", "")).upper()
            if observation.actual.get("operation_id") and state in _SUCCESS_STATES:
                return Verdict(is_pass=True, is_candidate_anomaly=False)
            return Verdict(is_pass=False, is_candidate_anomaly=False, category="OPERATION_FINALITY", reason="Write operation did not report a terminal success state", actual=observation.actual)

        if event.kind.value != "recall":
            return Verdict(is_pass=True, is_candidate_anomaly=False)

        entity, field = event.entity, event.field or "general"
        mode = event.mode or "current"
        actual_text = str(observation.actual.get("answer") or observation.actual.get("raw_response", ""))
        expected = event.expected
        if expected is None and mode == "current":
            expected = await self.oracle_db.get_current_fact(entity, field)

        if mode == "forgotten":
            historical_values = [value for value in await self.oracle_db.get_fact_history(entity, field) if value is not None]
            forbidden_values = ([expected] if expected is not None else []) + historical_values
            if not await self.oracle_db.is_forgotten(entity, field):
                return Verdict(is_pass=False, is_candidate_anomaly=False, category="TEST_HARNESS", reason="Forgotten recall has no forgotten oracle fact")
            if any(str(value).lower() in actual_text.lower() for value in forbidden_values):
                return Verdict(is_pass=False, is_candidate_anomaly=True, category="FORGET_RESURRECTION", reason="A forgotten value resurfaced in the answer", expected="Value absent", actual=actual_text)
            return Verdict(is_pass=True, is_candidate_anomaly=False)

        if mode == "historical" and expected is None:
            expected = await self.oracle_db.get_fact_history(entity, field)
        expected_values = expected if isinstance(expected, list) else [expected]
        if expected is None or not all(str(value).strip().lower() in actual_text.lower() for value in expected_values):
            return Verdict(is_pass=False, is_candidate_anomaly=True, category="MEMORY_RECALL_MISMATCH", reason=f"Expected {expected!r} not found in actual response {actual_text!r}", expected=expected, actual=actual_text)
        return Verdict(is_pass=True, is_candidate_anomaly=False, expected=expected, actual=actual_text)
