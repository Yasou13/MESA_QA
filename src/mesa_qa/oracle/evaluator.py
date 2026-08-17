from mesa_qa.judge.recall_matcher import match_recall
from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict
from mesa_qa.oracle.db import OracleDB

_WRITE_KINDS = {
    "remember",
    "correct",
    "forget",
    "duplicate",
    "semantic_duplicate",
    "multi_fact",
    "conflict",
    "idempotency",
}
_SUCCESS_STATES = {"COMMITTED"}


class OracleEvaluator:
    def __init__(self, oracle_db: OracleDB):
        self.oracle_db = oracle_db

    async def evaluate_observation(
        self, event: ScenarioEvent, observation: TesterObservation
    ) -> Verdict:
        if observation.tester_assessment == "infra_error":
            return Verdict(
                is_pass=False,
                is_candidate_anomaly=False,
                category="INFRASTRUCTURE",
                reason=observation.reason or "MCP/HTTP infrastructure error",
            )

        if event.kind.value in _WRITE_KINDS:
            state = str(observation.actual.get("operation_state", "")).upper()
            if observation.actual.get("operation_id") and state in _SUCCESS_STATES:
                return Verdict(is_pass=True, is_candidate_anomaly=False)

            error_data = (
                observation.actual.get("error")
                if isinstance(observation.actual.get("error"), dict)
                else {}
            )
            error_code = str(error_data.get("code", "")).upper()
            error_msg = str(error_data.get("message", "")).lower()

            if (
                state in {"REJECTED", "DENIED"}
                or "policy" in error_code.lower()
                or "policy" in error_msg
                or "denied" in error_code.lower()
            ):
                return Verdict(
                    is_pass=False,
                    is_candidate_anomaly=False,
                    category="EXPECTED_POLICY",
                    reason=f"Operation policy rejection ({state}): {error_msg or error_code or 'rejected'}",
                    actual=observation.actual,
                )

            if (
                "rate_limit" in error_code.lower()
                or "provider" in error_code.lower()
                or "rate limit" in error_msg
                or "provider" in error_msg
                or state == "TIMEOUT"
            ):
                return Verdict(
                    is_pass=False,
                    is_candidate_anomaly=False,
                    category="INFRASTRUCTURE",
                    reason=f"External provider / infrastructure issue ({state}): {error_msg or error_code or 'unavailable'}",
                    actual=observation.actual,
                )

            return Verdict(
                is_pass=False,
                is_candidate_anomaly=True,
                category="CANDIDATE_ANOMALY",
                reason=f"Write operation failed with terminal state {state}: {error_msg or 'internal failure'}",
                actual=observation.actual,
            )

        if event.kind.value != "recall":
            return Verdict(is_pass=True, is_candidate_anomaly=False)

        entity, field = event.entity, event.field or "general"
        mode = event.mode or "current"
        actual_text = str(
            observation.actual.get("answer")
            or observation.actual.get("raw_response", "")
        )
        expected = event.expected
        if expected is None and mode == "current":
            expected = await self.oracle_db.get_current_fact(entity, field)

        if mode == "forgotten":
            historical_values = [
                value
                for value in await self.oracle_db.get_fact_history(entity, field)
                if value is not None
            ]
            forbidden_values = (
                [expected] if expected is not None else []
            ) + historical_values
            if not await self.oracle_db.is_forgotten(entity, field):
                return Verdict(
                    is_pass=False,
                    is_candidate_anomaly=False,
                    category="TEST_HARNESS",
                    reason="Forgotten recall has no forgotten oracle fact",
                )
            if any(
                str(value).lower() in actual_text.lower() for value in forbidden_values
            ):
                return Verdict(
                    is_pass=False,
                    is_candidate_anomaly=True,
                    category="FORGET_RESURRECTION",
                    reason="A forgotten value resurfaced in the answer",
                    expected="Value absent",
                    actual=actual_text,
                )
            return Verdict(is_pass=True, is_candidate_anomaly=False)

        if mode == "historical" and expected is None:
            expected = await self.oracle_db.get_historical_facts(entity, field)

        is_pass, category, reason = match_recall(
            expected=expected,
            actual_text=actual_text,
            structured_actual=observation.actual,
        )
        return Verdict(
            is_pass=is_pass,
            is_candidate_anomaly=not is_pass,
            category=category,
            reason=reason,
            expected=expected,
            actual=actual_text,
        )
