from mesa_qa.config import SafetySettings
from mesa_qa.models import BugReport, Severity
from mesa_qa.repair.policy import RepairPolicyGuard
from mesa_qa.repair.gate import RepairGate

import subprocess

def test_repair_gate_success(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    safety = SafetySettings()
    policy = RepairPolicyGuard(safety)
    gate = RepairGate(policy)

    bug = BugReport(
        bug_id="BUG-0001",
        run_id="run1",
        severity=Severity.P1,
        category="MEMORY_RECALL",
        scenario_id="s1",
        expected={"expected": "Spring Boot"},
        actual={"actual": "FastAPI"},
        candidate_commit_before="sha1",
    )

    ok, reason = gate.evaluate_gates(bug, tmp_path, stable_reproduction_proven=True, pre_fix_test_exists=True)
    assert ok is True
    assert "PASSED" in reason

def test_repair_gate_fails_unreproducible(tmp_path):
    safety = SafetySettings()
    policy = RepairPolicyGuard(safety)
    gate = RepairGate(policy)

    bug = BugReport(
        bug_id="BUG-0001",
        run_id="run1",
        severity=Severity.P1,
        category="MEMORY_RECALL",
        scenario_id="s1",
        expected={"expected": "Spring Boot"},
        actual={"actual": "FastAPI"},
        candidate_commit_before="sha1",
    )

    ok, reason = gate.evaluate_gates(bug, tmp_path, stable_reproduction_proven=False, pre_fix_test_exists=True)
    assert ok is False
    assert "G1 failed" in reason
