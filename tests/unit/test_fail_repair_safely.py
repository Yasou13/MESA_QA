from __future__ import annotations

from pathlib import Path
import subprocess
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


@pytest.mark.asyncio
async def test_missing_pre_fix_test_halts_repair_as_needs_review(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True

    controller = QAController(cfg, run_id="run-missing-pre-fix")
    await controller.controller_db.initialize()

    # Bug report with NO pre_fix_test_file in preconditions
    bug = BugReport(
        bug_id="BUG-NO-TEST-1",
        run_id="run-missing-pre-fix",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-no-test",
        candidate_commit_before="abc1234",
        preconditions={},
    )
    event = ScenarioEvent(id="ev_nt1", entity="user", kind=ActionKind.REMEMBER, text="data nt1")

    controller.repairer.execute_repair = AsyncMock()
    controller.state_machine._current_state = State.CONFIRMED_BUG

    await controller._execute_repair_pipeline(bug, event)

    # Must halt as NEEDS_REVIEW without calling Repair Codex
    assert controller.state_machine.current == State.RUNNING
    controller.repairer.execute_repair.assert_not_called()
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "NEEDS_REVIEW"
    assert "missing genuine pre-fix regression" in controller._repairs[0]["reason"]


@pytest.mark.asyncio
async def test_valid_pre_fix_test_allows_repair_to_proceed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True

    controller = QAController(cfg, run_id="run-valid-pre-fix")
    await controller.controller_db.initialize()

    bug = BugReport(
        bug_id="BUG-VALID-TEST-1",
        run_id="run-valid-pre-fix",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-valid-test",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_repro.py"},
    )
    event = ScenarioEvent(id="ev_vt1", entity="user", kind=ActionKind.REMEMBER, text="data vt1")

    cand_wt = tmp_path / "cand_wt"
    cand_wt.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.com"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=cand_wt, capture_output=True, check=True)
    controller.process_mgr.candidate_worktree = cand_wt
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "genuine failure"))
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mod.py"])

    repair_result = RepairResult(bug_id="BUG-VALID-TEST-1", success=True, patch_content="diff", changed_files=["src/mod.py"])
    controller.repairer.execute_repair = AsyncMock(return_value=repair_result)
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=["tests/test_repro.py"])
    controller.repair_verifier.run_targeted_tests = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.commit_repair = MagicMock(return_value="repaired-commit-sha-999")
    controller.process_mgr.restart_all = AsyncMock()
    controller.tester.execute_action = AsyncMock(return_value=MagicMock(tools_called=["mesa_recall"], actual={"answer": "ok"}))
    controller.judge.judge = AsyncMock(return_value=MagicMock(is_pass=True, is_candidate_anomaly=False))

    controller.state_machine._current_state = State.CONFIRMED_BUG

    await controller._execute_repair_pipeline(bug, event)

    # Verify Repair Codex was invoked
    controller.repairer.execute_repair.assert_called_once()
    assert controller._repairs[0]["status"] == "VERIFIED"
