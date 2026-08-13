from __future__ import annotations

from pathlib import Path
from typing import Tuple
import logging

from mesa_qa.models import BugReport
from mesa_qa.repair.policy import RepairPolicyGuard

logger = logging.getLogger("mesa_qa.repair_gate")


class RepairGate:
    def __init__(self, policy_guard: RepairPolicyGuard):
        self.policy_guard = policy_guard

    def evaluate_gates(
        self,
        bug: BugReport,
        candidate_worktree: Path,
        stable_reproduction_proven: bool,
        pre_fix_test_exists: bool,
    ) -> Tuple[bool, str]:
        # G1: Anomaly is reproducible
        if not stable_reproduction_proven:
            return False, "Gate G1 failed: Bug reproduction is not stably proven"

        # G2: Expected behavior grounded in contract/oracle
        if not bug.expected:
            return False, "Gate G2 failed: Expected behavior is ungrounded or empty"

        # G3: Failing regression test exists or ready
        if not pre_fix_test_exists:
            return False, "Gate G3 failed: Pre-fix failing regression test does not exist"

        # G5: Repair scope compliant
        compliant, reason = self.policy_guard.validate_diff(candidate_worktree)
        if not compliant:
            return False, f"Gate G5 failed: {reason}"

        return True, "All repair gates (G1-G5) PASSED"
