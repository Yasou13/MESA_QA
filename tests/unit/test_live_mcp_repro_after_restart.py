from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_live_mcp_repro_passes_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-live-pass")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-LIVE-1",
        run_id="run-live-pass",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-l1",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_l1", entity="user", kind=ActionKind.REMEMBER, text="my secret key is 9999")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-LIVE-1", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/fix.py"])
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    controller.repair_verifier.commit_repair = MagicMock(return_value="commit-sha-999")
    controller.process_mgr.restart_all = AsyncMock()
    
    # Mock live repro execution
    mock_obs = MagicMock(action_id="live_BUG-LIVE-1", returncode=0, thread_id="t1", stdout="remembered 9999", stderr="")
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    
    mock_verdict = MagicMock(is_pass=True, is_candidate_anomaly=False)
    controller.judge.judge = AsyncMock(return_value=mock_verdict)
    
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    # Verify restart was called
    controller.process_mgr.restart_all.assert_called_once()
    
    # Verify tester and judge were invoked for live repro
    controller.tester.execute_action.assert_called_once_with(
        event, "live_BUG-LIVE-1", controller.run_dir / "tester_workspace"
    )
    controller.judge.judge.assert_called_once_with(event, mock_obs, controller.oracle_eval)
    
    # Verify outcome
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "VERIFIED"
    assert controller._repairs[0]["live_repro_passed"] is True


@pytest.mark.asyncio
async def test_live_mcp_repro_failure_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-live-fail")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-LIVE-2",
        run_id="run-live-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-l2",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_l2", entity="user", kind=ActionKind.REMEMBER, text="my secret key is 8888")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-LIVE-2", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/fix.py"])
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    controller.repair_verifier.commit_repair = MagicMock(return_value="commit-sha-888")
    controller.process_mgr.restart_all = AsyncMock()
    
    mock_obs = MagicMock(action_id="live_BUG-LIVE-2", returncode=1, thread_id="t1", stdout="error", stderr="fail")
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    
    mock_verdict = MagicMock(is_pass=False, is_candidate_anomaly=True)
    controller.judge.judge = AsyncMock(return_value=mock_verdict)
    
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "LIVE_REPRO_FAILED"
    assert controller._repairs[0]["live_repro_passed"] is False
