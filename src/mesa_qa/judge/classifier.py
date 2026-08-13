from __future__ import annotations

from mesa_qa.models import Severity, Verdict


class AnomalyClassifier:
    def classify(self, verdict: Verdict, event_kind: str) -> tuple[Severity, str]:
        category = verdict.category or "UNKNOWN"
        reason_lower = verdict.reason.lower()

        if "resurrect" in reason_lower or "forget" in category.lower():
            return Severity.P0, "FORGET_PURGE"

        if "crash" in reason_lower:
            return Severity.P0, "RUNTIME_CRASH"

        if "recall" in category.lower() or "retrieval" in category.lower():
            return Severity.P1, "MEMORY_RECALL"

        if event_kind == "correct":
            return Severity.P1, "CORRECT"

        if event_kind == "restart_runtime":
            return Severity.P1, "RESTART_DURABILITY"

        return Severity.P2, category
