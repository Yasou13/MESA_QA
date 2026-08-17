from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import aiosqlite
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_verified_persisted_strictly_after_live_mcp_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-verified-strictly")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-V1",
        run_id="run-verified-strictly",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-v1",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_v1", entity="user", kind=ActionKind.REMEMBER, text="data v1")
    
    # Initially record bug as CONFIRMED
    await controller.controller_db.record_bug(
        bug.bug_id,
        controller.run_id,
        bug.severity.value,
        bug.category,
        bug.model_dump(),
        "CONFIRMED",
        "2026-08-17T00:00:00Z",
    )
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-V1", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/fix.py"])
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    controller.repair_verifier.commit_repair = MagicMock(return_value="commit-sha-v1")
    controller.process_mgr.restart_all = AsyncMock()
    
    mock_obs = MagicMock(action_id="live_BUG-V1", returncode=0, thread_id="t1", stdout="remembered data v1", stderr="")
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    
    # Case 1: live repro PASS
    mock_verdict_pass = MagicMock(is_pass=True, is_candidate_anomaly=False)
    controller.judge.judge = AsyncMock(return_value=mock_verdict_pass)
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    # Query database directly to verify status is now VERIFIED
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-V1'") as cur:
            row = await cur.fetchone()
            assert row[0] == "VERIFIED"


@pytest.mark.asyncio
async def test_verified_not_persisted_on_live_repro_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-verified-not-fail")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-V2",
        run_id="run-verified-not-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-v2",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_v2", entity="user", kind=ActionKind.REMEMBER, text="data v2")
    
    await controller.controller_db.record_bug(
        bug.bug_id,
        controller.run_id,
        bug.severity.value,
        bug.category,
        bug.model_dump(),
        "CONFIRMED",
        "2026-08-17T00:00:00Z",
    )
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-V2", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/fix.py"])
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=[])
    controller.repair_verifier.commit_repair = MagicMock(return_value="commit-sha-v2")
    controller.process_mgr.restart_all = AsyncMock()
    
    mock_obs = MagicMock(action_id="live_BUG-V2", returncode=1, thread_id="t1", stdout="error", stderr="fail")
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    
    # Case 2: live repro FAIL
    mock_verdict_fail = MagicMock(is_pass=False, is_candidate_anomaly=True)
    controller.judge.judge = AsyncMock(return_value=mock_verdict_fail)
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    # Query database directly to verify status is LIVE_REPRO_FAILED, NEVER VERIFIED
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-V2'") as cur:
            row = await cur.fetchone()
            assert row[0] == "LIVE_REPRO_FAILED"
