import pytest

from mesa_qa.models import BugReport, Severity
from mesa_qa.repair.verification import RepairVerifier


def test_verifier_refuses_captured_data_regression(tmp_path):
    bug = BugReport(bug_id="BUG-1", run_id="run", severity=Severity.P1, category="MEMORY_RECALL", scenario_id="scenario", expected={"expected": "new"}, actual={"actual": "old"}, candidate_commit_before="sha")
    verifier = RepairVerifier(PathLikePython())
    with pytest.raises(RuntimeError, match="must not synthesize"):
        verifier.create_regression_test(tmp_path, bug)


def PathLikePython():
    from pathlib import Path
    return Path("/usr/bin/python3")
