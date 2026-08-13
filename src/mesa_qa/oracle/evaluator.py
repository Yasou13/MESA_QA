from __future__ import annotations

from typing import Any, Optional
from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict
from mesa_qa.oracle.db import OracleDB


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
                reason=observation.reason or "MCP/HTTP Infrastructure error",
            )

        if event.kind.value == "recall":
            entity = event.entity
            field = event.field or "general"
            mode = event.mode or "current"

            expected = event.expected
            if expected is None and mode == "current":
                expected = await self.oracle_db.get_current_fact(entity, field)

            actual_text = observation.actual.get("answer") or str(observation.actual.get("raw_response", ""))

            if mode == "forgotten":
                is_forgotten = await self.oracle_db.is_forgotten(entity, field)
                # Check that forgotten value does NOT resurrect
                if is_forgotten and expected and str(expected).lower() in actual_text.lower():
                    return Verdict(
                        is_pass=False,
                        is_candidate_anomaly=True,
                        category="FORGET_RESURRECTION",
                        reason=f"Forgotten value '{expected}' resurfaced in answer!",
                        expected="Value forgotten / absent",
                        actual=actual_text,
                    )
                return Verdict(is_pass=True, is_candidate_anomaly=False)

            if expected:
                normalized_expected = str(expected).strip().lower()
                normalized_actual = actual_text.strip().lower()

                if normalized_expected in normalized_actual:
                    return Verdict(
                        is_pass=True,
                        is_candidate_anomaly=False,
                        expected=expected,
                        actual=actual_text,
                    )
                else:
                    return Verdict(
                        is_pass=False,
                        is_candidate_anomaly=True,
                        category="MEMORY_RECALL_MISMATCH",
                        reason=f"Expected '{expected}' not found in actual response '{actual_text}'",
                        expected=expected,
                        actual=actual_text,
                    )

        # Default pass for write actions (remember, correct, forget)
        return Verdict(is_pass=True, is_candidate_anomaly=False)
