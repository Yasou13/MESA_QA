from __future__ import annotations

from typing import Any

from mesa_qa.judge.recall_matcher import match_recall
from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict


class DeterministicJudge:
    async def judge(
        self,
        event: ScenarioEvent,
        observation: TesterObservation,
        oracle_evaluator: Any,
    ) -> Verdict:
        if oracle_evaluator is not None:
            return await oracle_evaluator.evaluate_observation(event, observation)
        if observation.tester_assessment == "infra_error":
            return Verdict(
                is_pass=False,
                is_candidate_anomaly=False,
                category="INFRASTRUCTURE",
                reason=observation.reason or "MCP/HTTP infrastructure error",
            )
        if event.kind.value != "recall":
            state = str(observation.actual.get("operation_state", "")).upper()
            if observation.actual.get("operation_id") and state == "COMMITTED":
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
        actual = str(
            observation.actual.get("answer")
            or observation.actual.get("raw_response", "")
        )
        is_pass, category, reason = match_recall(
            expected=event.expected,
            actual_text=actual,
            structured_actual=observation.actual,
        )
        return Verdict(
            is_pass=is_pass,
            is_candidate_anomaly=not is_pass,
            category=category,
            reason=reason,
            expected=event.expected,
            actual=actual,
        )
