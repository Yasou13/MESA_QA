from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import aiosqlite
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_repair_live_recheck_success(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True

    controller = QAController(cfg, run_id="run-live-recheck-pass")
    await controller.controller_db.initialize()

    cand_wt = tmp_path / "cand_wt"
    cand_wt.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.com"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=cand_wt, capture_output=True, check=True)
    controller.process_mgr.candidate_worktree = cand_wt

    bug = BugReport(
        bug_id="BUG-LIVE-1",
        run_id="run-live-recheck-pass",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-live-1",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_repro.py"},
    )
    event = ScenarioEvent(id="ev_l1", entity="user", kind=ActionKind.REMEMBER, text="data l1")

    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mod.py"])

    repair_result = RepairResult(bug_id="BUG-LIVE-1", success=True, patch_content="diff", changed_files=["src/mod.py"])
    controller.repairer.execute_repair = AsyncMock(return_value=repair_result)
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=["tests/test_repro.py"])
    controller.repair_verifier.run_targeted_tests = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.commit_repair = MagicMock(return_value="repaired-commit-sha-111")
    controller.process_mgr.restart_all = AsyncMock()

    # Live check returns PASS
    controller.tester.execute_action = AsyncMock(return_value=MagicMock(tools_called=["mesa_recall"], actual={"answer": "correct"}))
    controller.judge.judge = AsyncMock(return_value=MagicMock(is_pass=True, is_candidate_anomaly=False))

    controller.state_machine._current_state = State.CONFIRMED_BUG

    await controller._execute_repair_pipeline(bug, event)

    assert controller.state_machine.current == State.RUNNING
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "VERIFIED"
    assert controller._repairs[0]["commit_sha"] == "repaired-commit-sha-111"
    assert controller._repairs[0]["live_repro_passed"] is True

    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-LIVE-1'") as cur:
            row = await cur.fetchone()
            assert row[0] == "VERIFIED"


@pytest.mark.asyncio
async def test_repair_live_recheck_failure_blocks_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.repair.enabled = True

    controller = QAController(cfg, run_id="run-live-recheck-fail")
    await controller.controller_db.initialize()

    cand_wt = tmp_path / "cand_wt"
    cand_wt.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.com"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=cand_wt, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=cand_wt, capture_output=True, check=True)
    controller.process_mgr.candidate_worktree = cand_wt

    bug = BugReport(
        bug_id="BUG-LIVE-2",
        run_id="run-live-recheck-fail",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-live-2",
        candidate_commit_before="abc1234",
        preconditions={"pre_fix_test_file": "tests/test_repro.py"},
    )
    event = ScenarioEvent(id="ev_l2", entity="user", kind=ActionKind.REMEMBER, text="data l2")

    controller.repair_verifier.verify_pre_fix_failure = MagicMock(return_value=(True, "failed"))
    controller.repair_verifier.run_pytest_on_file = MagicMock(return_value=(True, "passed"))
    controller.repair_gate.evaluate_gates = MagicMock(return_value=(True, "ok"))
    controller.process_mgr.worktree_mgr.capture_candidate_snapshot = MagicMock(return_value={"main_baseline": {}})
    controller.process_mgr.worktree_mgr.assert_main_unchanged = MagicMock()
    controller.process_mgr.worktree_mgr.assert_candidate_identity = MagicMock()
    controller.policy_guard.validate_diff = MagicMock(return_value=(True, "ok"))
    controller.policy_guard.changed_paths = MagicMock(return_value=["src/mod.py"])

    repair_result = RepairResult(bug_id="BUG-LIVE-2", success=True, patch_content="diff", changed_files=["src/mod.py"])
    controller.repairer.execute_repair = AsyncMock(return_value=repair_result)
    controller.repair_verifier.find_targeted_tests = MagicMock(return_value=["tests/test_repro.py"])
    controller.repair_verifier.run_targeted_tests = MagicMock(return_value=(True, "passed"))
    controller.repair_verifier.commit_repair = MagicMock(return_value="repaired-commit-sha-222")
    controller.process_mgr.restart_all = AsyncMock()

    # Live check FAILS post-restart
    controller.tester.execute_action = AsyncMock(return_value=MagicMock(tools_called=["mesa_recall"], actual={"answer": "wrong"}))
    controller.judge.judge = AsyncMock(return_value=MagicMock(is_pass=False, is_candidate_anomaly=True, reason="mismatch"))

    controller.state_machine._current_state = State.CONFIRMED_BUG

    await controller._execute_repair_pipeline(bug, event)

    assert controller.state_machine.current == State.RUNNING
    assert len(controller._repairs) == 1
    assert controller._repairs[0]["status"] == "LIVE_REPRO_FAILED"
    assert controller._repairs[0]["live_repro_passed"] is False

    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-LIVE-2'") as cur:
            row = await cur.fetchone()
            assert row[0] == "LIVE_REPRO_FAILED"
            assert row[0] != "VERIFIED"
