from __future__ import annotations

from typing import Any, Optional
from mesa_qa.models import ScenarioEvent, TesterObservation, Verdict


class DeterministicJudge:
    def judge(self, event: ScenarioEvent, observation: TesterObservation, oracle_evaluator: Any) -> Verdict:
        # High-priority check for infrastructure errors
        if observation.tester_assessment == "infra_error":
            return Verdict(
                is_pass=False,
                is_candidate_anomaly=False,
                category="INFRASTRUCTURE",
                reason=observation.reason or "MCP/HTTP Infrastructure Error",
            )

        # Non-recall write events pass by default unless reported as error
        if event.kind.value != "recall":
            return Verdict(is_pass=True, is_candidate_anomaly=False)

        # Recall evaluation against expected truth
        actual_text = observation.actual.get("answer") or str(observation.actual.get("raw_response", ""))
        expected = event.expected

        if expected is not None:
            exp_str = str(expected).strip().lower()
            act_str = actual_text.strip().lower()

            if exp_str in act_str:
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
                    category="RETRIEVAL_MISMATCH",
                    reason=f"Expected substring '{expected}' not found in actual response: '{actual_text}'",
                    expected=expected,
                    actual=actual_text,
                )

        return Verdict(is_pass=True, is_candidate_anomaly=False)
