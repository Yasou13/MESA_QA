from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_controller_kill_and_resume_e2e(tmp_path, monkeypatch):
    """
    S049: Controller kill and resume acceptance.
    Proves that an abrupt controller termination preserves run state,
    Oracle ground truth, candidate identity, and resumes cleanly without
    state corruption.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = False
    
    run_id = "run-kill-resume-e2e"
    
    # 1. Setup candidate worktree
    cand_wt = tmp_path / "candidate_worktree"
    cand_wt.mkdir(parents=True, exist_ok=True)
    
    # --- Session 1: Run initially and process actions ---
    ctrl1 = QAController(cfg, run_id=run_id)
    await ctrl1.controller_db.initialize()
    await ctrl1.oracle_db.initialize()
    
    # Mock worktree manager & candidate runtime
    ctrl1.process_mgr.worktree_mgr._run_git = lambda cwd, args: "cand_head_sha_999"
    ctrl1.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "main_head_sha", "is_clean": True, "branch": "main"}
    ctrl1.process_mgr.worktree_mgr.capture_main_baseline = lambda: {"head": "main_head_sha", "status": ""}
    ctrl1.process_mgr.worktree_mgr.assert_main_unchanged = lambda baseline: None
    ctrl1.process_mgr.start_all = AsyncMock()
    ctrl1.process_mgr.stop_all = AsyncMock()
    
    ctrl1._started_at = "2026-08-17T13:00:00+00:00"
    ctrl1._epoch = 0
    ctrl1._action_count = 5
    ctrl1.tester.thread_id = "thread_session_1"
    ctrl1.process_mgr.candidate_worktree = cand_wt
    ctrl1.process_mgr.candidate_branch = "qa/autonomous-kill-resume"
    ctrl1.process_mgr.candidate_base_sha = "cand_base_sha_999"
    
    ctrl1.scenario_engine.load_suite()
    ctrl1.scenario_engine.cursor = 5
    
    # Apply ground truth to Oracle in Session 1
    event1 = ScenarioEvent(id="ev_kill_01", entity="service:auth", field="port", kind=ActionKind.REMEMBER, text="Auth port is 8080", value="8080")
    await ctrl1.oracle_db.apply_event(event1)
    
    # Persist state
    await ctrl1._persist_state()
    
    # --- Simulate Abrupt Controller Termination (Kill / Crash) ---
    # In-memory controller1 is abandoned without running stop() or shutdown().
    del ctrl1
    
    # --- Session 2: Resumed Controller ---
    ctrl2 = QAController(cfg, run_id=run_id)
    await ctrl2.controller_db.initialize()
    await ctrl2.oracle_db.initialize()
    
    ctrl2.process_mgr.worktree_mgr._run_git = lambda cwd, args: "cand_head_sha_999"
    ctrl2.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "main_head_sha", "is_clean": True, "branch": "main"}
    ctrl2.process_mgr.worktree_mgr.capture_main_baseline = lambda: {"head": "main_head_sha", "status": ""}
    ctrl2.process_mgr.worktree_mgr.assert_main_unchanged = lambda baseline: None
    ctrl2.process_mgr.start_all = AsyncMock()
    ctrl2.process_mgr.stop_all = AsyncMock()
    
    # Execute resume from crash
    await ctrl2.resume_from_crash()
    
    # Verify state, invariants and cursor restored
    assert ctrl2._started_at == "2026-08-17T13:00:00+00:00"
    assert ctrl2._epoch == 0
    assert ctrl2._action_count == 5
    assert ctrl2.tester.thread_id == "thread_session_1"
    assert ctrl2.scenario_engine.cursor == 5
    assert ctrl2.process_mgr.candidate_worktree == cand_wt
    assert ctrl2.process_mgr.candidate_branch == "qa/autonomous-kill-resume"
    assert ctrl2.process_mgr.candidate_base_sha == "cand_base_sha_999"
    assert ctrl2.state_machine.current == State.RUNNING
    
    # Verify Oracle DB ground truth remained intact across kill
    oracle_val = await ctrl2.oracle_db.get_current_fact("service:auth", "port")
    assert oracle_val is not None
    assert oracle_val == "8080"
    
    # Process next action in resumed session
    mock_obs = TesterObservation(
        action_id="act_resumed_06",
        scenario_event_id="ev_kill_02",
        tools_called=["mesa_recall"],
        actual={"answer": "Auth port is 8080"},
        tester_assessment="pass",
        reason="ok",
        raw_output="Auth port is 8080",
        exit_code=0,
        thread_id="thread_session_1",
    )
    ctrl2.tester.execute_action = AsyncMock(return_value=mock_obs)
    ctrl2.judge.judge = AsyncMock(return_value=MagicMock(is_pass=True, is_candidate_anomaly=False, reason="ok"))
    
    event2 = ctrl2.scenario_engine.next_event()
    await ctrl2._process_event(event2)
    
    # Verify action count incremented cleanly and cursor advanced
    assert ctrl2._action_count == 6
    assert ctrl2.scenario_engine.cursor == 6
    
    # Perform clean shutdown on resumed controller
    await ctrl2.shutdown()
    assert ctrl2.process_mgr.stop_all.called
