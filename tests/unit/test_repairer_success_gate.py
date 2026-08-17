from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_repairer_failure_blocks_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-repair-succ-fail")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-0001",
        run_id="run-repair-succ-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-01",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev1", entity="user", kind=ActionKind.REMEMBER, text="fact")
    
    # Mock pre_fix fail verification to succeed (confirming genuine fail)
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    # Mock Repairer to return success=False
    failed_result = RepairResult(bug_id="BUG-0001", success=False, error_message="could not synthesize valid patch")
    controller.repairer.execute_repair = AsyncMock(return_value=failed_result)
    
    controller.repair_verifier.commit_repair = MagicMock()
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    # Assert commit_repair was NEVER called
    controller.repair_verifier.commit_repair.assert_not_called()
    assert failed_result.success is False
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "REPAIR_FAILED"
    assert "success=false" in controller._repairs[0]["reason"]


@pytest.mark.asyncio
async def test_repairer_success_allows_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-repair-succ-pass")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-0002",
        run_id="run-repair-succ-pass",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-02",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev2", entity="user", kind=ActionKind.REMEMBER, text="fact")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    # Mock Repairer to return success=True
    success_result = RepairResult(bug_id="BUG-0002", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    # Mock post-fix test pass
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    controller.repair_verifier.commit_repair = MagicMock(return_value="commit-sha-123")
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/fix.py"])
    controller.process_mgr.restart_all = AsyncMock()
    
    mock_obs = MagicMock(action_id="live_BUG-0002", returncode=0, thread_id="t1", stdout="ok", stderr="")
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    mock_verdict = MagicMock(is_pass=True, is_candidate_anomaly=False)
    controller.judge.judge = AsyncMock(return_value=mock_verdict)
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    controller.repair_verifier.commit_repair.assert_called_once()
    assert success_result.success is True
    assert success_result.commit_sha == "commit-sha-123"
    assert controller._repairs[0]["status"] == "VERIFIED"
