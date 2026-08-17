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
async def test_unexpected_exception_fails_repair_safely(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-safe-fail-exc")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-SAFE-1",
        run_id="run-safe-fail-exc",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-s1",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_s1", entity="user", kind=ActionKind.REMEMBER, text="data s1")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    # Repairer throws an unexpected exception (e.g. LLM network error / crash)
    controller.repairer.execute_repair = AsyncMock(side_effect=RuntimeError("LLM API network disconnect"))
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    # Must not raise unhandled exception
    await controller._execute_repair_pipeline(bug, event)
    
    # Verify state machine returned to RUNNING
    assert controller.state_machine.current == State.RUNNING
    
    # Verify repair record
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "REPAIR_FAILED"
    assert "LLM API network disconnect" in controller._repairs[0]["reason"]
    
    # Verify database status is REPAIR_FAILED and not VERIFIED
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-SAFE-1'") as cur:
            row = await cur.fetchone()
            assert row[0] == "REPAIR_FAILED"


@pytest.mark.asyncio
async def test_policy_violation_recorded_safely(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-safe-fail-policy")
    await controller.controller_db.initialize()
    
    bug = BugReport(
        bug_id="BUG-SAFE-2",
        run_id="run-safe-fail-policy",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-s2",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_bug.py"},
    )
    event = ScenarioEvent(id="ev_s2", entity="user", kind=ActionKind.REMEMBER, text="data s2")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    success_result = RepairResult(bug_id="BUG-SAFE-2", success=True)
    controller.repairer.execute_repair = AsyncMock(return_value=success_result)
    
    # Policy guard rejects diff
    controller.policy_guard.validate_diff = MagicMock(return_value=(False, "changed lines 800 exceeds limit 300"))
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    await controller._execute_repair_pipeline(bug, event)
    
    assert controller.state_machine.current == State.RUNNING
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "POLICY_VIOLATION"
    
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-SAFE-2'") as cur:
            row = await cur.fetchone()
            assert row[0] == "POLICY_VIOLATION"
