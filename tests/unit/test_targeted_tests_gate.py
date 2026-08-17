from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.repair.verification import RepairVerifier
from mesa_qa.state_machine import State


def test_find_targeted_tests(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir(parents=True, exist_ok=True)
    tests_dir = wt / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    
    (tests_dir / "test_storage.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tests_dir / "test_other.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    
    verifier = RepairVerifier(python_bin=Path("/usr/bin/python3"))
    
    # Passing modified src/mesa/storage.py should discover tests/test_storage.py
    targeted = verifier.find_targeted_tests(wt, ["src/mesa/storage.py"])
    assert "tests/test_storage.py" in targeted
    assert "tests/test_other.py" not in targeted


@pytest.mark.asyncio
async def test_targeted_test_failure_blocks_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-targeted-fail")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-T1",
        run_id="run-targeted-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-t1",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev1", entity="user", kind=ActionKind.REMEMBER, text="fact")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-T1", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/storage.py"])
    
    # Genuine regression test passes
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    
    # Targeted tests discovered and fail
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=["tests/test_storage.py"])
    controller.repair_verifier.run_targeted_tests = MagicMock(return_value=(False, "AssertionError in storage"))
    
    controller.repair_verifier.commit_repair = MagicMock()
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    controller.repair_verifier.commit_repair.assert_not_called()
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "TARGETED_TESTS_FAILED"


@pytest.mark.asyncio
async def test_full_suite_failure_blocks_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    cfg.verification.run_full_suite = True
    
    controller = QAController(cfg, run_id="run-full-fail")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-T2",
        run_id="run-full-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-t2",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev2", entity="user", kind=ActionKind.REMEMBER, text="fact")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-T2", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/storage.py"])
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    
    # Full suite fails
    controller.repair_verifier.run_full_suite = MagicMock(return_value=(False, "Suite failed"))
    controller.repair_verifier.commit_repair = MagicMock()
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    controller.repair_verifier.commit_repair.assert_not_called()
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "FULL_SUITE_FAILED"
