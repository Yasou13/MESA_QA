from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import aiosqlite
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.state_machine import State
from mesa_qa.telemetry.reports import ReportBuilder


@pytest.mark.asyncio
async def test_controlled_candidate_repair_e2e_full_chain(tmp_path, monkeypatch):
    """
    S048: Controlled candidate-only repair E2E.
    Proves the complete chain:
      anomaly -> reproduction -> CONFIRMED_BUG -> PRE-FIX FAIL ->
      pre-repair snapshot & identity gate -> Repair ->
      post-repair identity & bounded diff gate -> tests pass ->
      approved paths commit -> restart -> live MCP repro PASS -> VERIFIED -> report.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    cfg.repair.max_repairs_per_run = 5
    
    run_id = "run-e2e-repair-controlled"
    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()
    
    # Setup controlled worktree mock
    wt_path = tmp_path / "candidate_wt"
    wt_path.mkdir(parents=True, exist_ok=True)
    controller.process_mgr.worktree_path = wt_path
    
    # 1. Controlled defect event
    event = ScenarioEvent(
        id="evt_ctrl_01",
        entity="project:athena",
        kind=ActionKind.RECALL,
        question="What is the Athena database?",
        expected_fact="PostgreSQL",
    )
    
    bug = BugReport(
        bug_id="BUG-CTRL-01",
        run_id=run_id,
        severity=Severity.P1,
        category="MEMORY_RECALL",
        scenario_id="scen_ctrl",
        candidate_commit_before="sha_before_123",
        reproduction_path=str(tmp_path / "evidence" / "BUG-CTRL-01"),
        preconditions={"pre_fix_test_file": "tests/test_athena_db.py"},
    )
    
    # Mock pre-fix fail: (True, "1 failed as expected")
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(
        return_value=(True, "PASSED pre-fix failure check: 1 failed")
    )
    
    # Mock pre/post identity gates
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "Gates OK"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(
        return_value={
            "candidate_head": "sha_before_123",
            "candidate_branch": "qa/autonomous-ctrl",
            "candidate_status": "",
            "candidate_tracked_diff": "",
            "candidate_untracked_files": [],
            "main_baseline": {"head": "main_sha_abc", "status": ""},
        }
    )
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    # Mock repairer execution
    repair_result = RepairResult(
        bug_id="BUG-CTRL-01",
        success=True,
        changed_files=["src/mesa/retrieval/memory.py"],
        patch="--- a/src/mesa/retrieval/memory.py\n+++ b/src/mesa/retrieval/memory.py\n@@ -10,1 +10,1 @@\n-old\n+new\n",
        explanation="Fixed fact extraction and recall matching for project entities.",
    )
    controller.repairer.execute_repair = AsyncMock(return_value=repair_result)
    
    # Mock policy guard and bounded diff
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "Diff bounded (1 file, +1/-1 lines)"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mesa/retrieval/memory.py"])
    
    # Mock test suite passes
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "1 passed"))
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=["tests/test_memory.py"])
    controller.repair_verifier.run_targeted_tests = MagicMock(return_value=(True, "2 passed"))
    
    # Mock repair commit
    controller.repair_verifier.commit_repair = MagicMock(return_value="sha_after_456")
    
    # Mock candidate restart
    controller.process_mgr.restart_all = AsyncMock()
    
    # Mock identical live MCP reproduction PASS
    mock_live_obs = MagicMock(
        action_id="live_BUG-CTRL-01",
        returncode=0,
        thread_id="thread_ctrl_new",
        stdout="The database for Athena is PostgreSQL.",
        stderr="",
        tools_called=["mesa_recall"],
    )
    controller.tester.execute_action = AsyncMock(return_value=mock_live_obs)
    mock_judge_pass = MagicMock(is_pass=True, is_candidate_anomaly=False)
    controller.judge.judge = AsyncMock(return_value=mock_judge_pass)
    
    # Set state machine to CONFIRMED_BUG
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    # Record initial confirmed bug
    await controller.controller_db.record_bug(
        bug.bug_id,
        run_id,
        bug.severity.value,
        bug.category,
        bug.model_dump(),
        "CONFIRMED",
        "2026-08-17T13:00:00Z",
    )
    controller._bugs.append({"bug_id": bug.bug_id, "status": "CONFIRMED", "severity": bug.severity.value})
    
    # Execute full repair pipeline
    await controller._execute_repair_pipeline(bug, event)
    
    # Assertions on pipeline ordering and outcomes:
    # 1. Pre-fix fail was checked
    controller.repair_verifier.verify_pre_fix_failure.assert_called_once()
    
    # 2. Repairer was called
    controller.repairer.execute_repair.assert_called_once()
    
    # 3. Diff was checked
    controller.policy_guard.validate_diff.assert_called_once()
    
    # 4. Targeted tests were run
    controller.repair_verifier.run_targeted_tests.assert_called_once()
    
    # 5. Commit occurred
    controller.repair_verifier.commit_repair.assert_called_once()
    
    # 6. Candidate restarted
    controller.process_mgr.restart_all.assert_called_once()
    
    # 7. Live MCP reproduction was run
    controller.tester.execute_action.assert_called_once()
    
    # 8. Status in DB is VERIFIED
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-CTRL-01'") as cur:
            row = await cur.fetchone()
            assert row[0] == "VERIFIED"
            
    # 9. Final report derived verdict
    report_builder = ReportBuilder(controller.run_dir)
    state_dict = await controller.controller_db.get_run_state(run_id) or {
        "run_id": run_id,
        "status": "COMPLETED",
        "action_count": 1,
    }
    state_dict["status"] = "COMPLETED"
    state_dict["action_count"] = 1
    
    # Update bug status in controller list to match DB
    controller._bugs[0]["status"] = "VERIFIED"
    bugs = controller._bugs
    repairs = controller._repairs
    
    assert len(repairs) == 1
    assert repairs[0]["status"] == "VERIFIED"
    assert repairs[0]["commit_sha"] == "sha_after_456"
    
    md_path, json_path = report_builder.generate_final_report(state_dict, bugs, repairs)
    assert md_path.exists()
    assert json_path.exists()
    
    verdict = report_builder.derive_session_verdict(state_dict, bugs, repairs)
    assert verdict == "PASS"


@pytest.mark.asyncio
async def test_controlled_repair_rejected_on_pre_fix_not_failing(tmp_path, monkeypatch):
    """If candidate test doesn't fail pre-fix, repair must not proceed."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-reject-prefix")
    await controller.controller_db.initialize()
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    bug = BugReport(
        bug_id="BUG-REJECT-01",
        run_id="run-reject-prefix",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen",
        candidate_commit_before="sha1",
        preconditions={"pre_fix_test_file": "tests/test.py"},
    )
    event = ScenarioEvent(id="ev1", entity="user", kind=ActionKind.RECALL, text="data")
    
    # Pre-fix check reports pseudo-test passing
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(
        return_value=(False, "FAILED pre-fix check: test unexpectedly passed")
    )
    controller.repairer.execute_repair = AsyncMock()
    
    await controller._execute_repair_pipeline(bug, event)
    
    # Repair must not execute
    controller.repairer.execute_repair.assert_not_called()
    assert controller._repairs[0]["status"] == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_controlled_repair_rejected_on_diff_out_of_bounds(tmp_path, monkeypatch):
    """If repair diff exceeds bounded limits, commit must be blocked."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True
    
    controller = QAController(cfg, run_id="run-reject-diff")
    await controller.controller_db.initialize()
    controller.state_machine._current_state = State.CONFIRMED_BUG
    
    bug = BugReport(
        bug_id="BUG-REJECT-02",
        run_id="run-reject-diff",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen",
        candidate_commit_before="sha1",
        preconditions={"pre_fix_test_file": "tests/test.py"},
    )
    event = ScenarioEvent(id="ev1", entity="user", kind=ActionKind.RECALL, text="data")
    
    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    
    controller.repairer.execute_repair = AsyncMock(
        return_value=RepairResult(bug_id="BUG-REJECT-02", success=True, changed_files=["f1.py", "f2.py", "f3.py", "f4.py"])
    )
    controller.policy_guard.validate_diff = MagicMock(return_value=(False, "Max changed files exceeded (4 > 3)"))
    controller.repair_verifier.commit_repair = MagicMock()
    
    await controller._execute_repair_pipeline(bug, event)
    
    controller.repair_verifier.commit_repair.assert_not_called()
    assert controller._repairs[0]["status"] == "POLICY_VIOLATION"
