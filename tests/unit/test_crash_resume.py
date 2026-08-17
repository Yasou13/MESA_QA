from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_resume_from_crash_restores_all_state_and_invariants(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    run_id = "run-resume-success"

    # Create dummy candidate worktree with git HEAD
    cand_wt = tmp_path / "cand_wt"
    cand_wt.mkdir()
    (cand_wt / ".git").mkdir()

    controller1 = QAController(cfg, run_id=run_id)
    await controller1.controller_db.initialize()
    await controller1.oracle_db.initialize()

    # Mock worktree manager git
    controller1.process_mgr.worktree_mgr._run_git = lambda cwd, args: "head-sha-12345"

    controller1._started_at = "2026-08-16T12:00:00+00:00"
    controller1._epoch = 3
    controller1._action_count = 42
    controller1.tester.thread_id = "thread_resume_abc"
    controller1.process_mgr.candidate_worktree = cand_wt
    controller1.process_mgr.candidate_branch = "candidate/run-resume-success"
    controller1.process_mgr.candidate_base_sha = "base-sha-12345"
    controller1._main_baseline = {
        "head": "main-head-original",
        "status": "",
        "tracked_diff": "",
        "toplevel": "/mock/main",
        "common_dir": "/mock/main/.git",
        "untracked_files": "",
    }
    controller1.evidence_store.save_json("main_baseline.json", controller1._main_baseline)

    controller1.scenario_engine.load_suite()
    controller1.scenario_engine.cursor = 10

    # Persist state
    await controller1._persist_state()

    # Now create a fresh controller simulating a crash restart
    controller2 = QAController(cfg, run_id=run_id)
    controller2.process_mgr.worktree_mgr._run_git = lambda cwd, args: "head-sha-12345"
    controller2.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "main-head-original", "is_clean": True, "branch": "main"}
    controller2.process_mgr.worktree_mgr.capture_main_baseline = lambda: dict(controller1._main_baseline)
    controller2.process_mgr.start_all = AsyncMock()

    await controller2.resume_from_crash()

    # Verify all fields restored
    assert controller2._started_at == "2026-08-16T12:00:00+00:00"
    assert controller2._epoch == 3
    assert controller2._action_count == 42
    assert controller2.tester.thread_id == "thread_resume_abc"
    assert controller2.scenario_engine.cursor == 10
    assert controller2.process_mgr.candidate_worktree == cand_wt
    assert controller2.process_mgr.candidate_branch == "candidate/run-resume-success"
    assert controller2.process_mgr.candidate_base_sha == "base-sha-12345"
    assert controller2._main_baseline == controller1._main_baseline
    assert controller2.state_machine.current == State.RUNNING


@pytest.mark.asyncio
async def test_resume_from_crash_missing_worktree_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    run_id = "run-resume-missing-wt"

    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()
    await controller.controller_db.save_run_state({
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": "2026-08-16T12:00:00+00:00",
        "candidate_worktree": str(tmp_path / "nonexistent_wt"),
        "candidate_head": "abc",
        "baseline_main_head": "main-head-001",
    })

    controller_resumed = QAController(cfg, run_id=run_id)
    with pytest.raises(FileNotFoundError, match="candidate worktree does not exist"):
        await controller_resumed.resume_from_crash()


@pytest.mark.asyncio
async def test_resume_from_crash_head_mismatch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    run_id = "run-resume-head-mismatch"

    cand_wt = tmp_path / "cand_wt_mutated"
    cand_wt.mkdir()

    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()
    await controller.controller_db.save_run_state({
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": "2026-08-16T12:00:00+00:00",
        "candidate_worktree": str(cand_wt),
        "candidate_head": "expected-sha-999",
        "baseline_main_head": "main-head-001",
    })

    controller_resumed = QAController(cfg, run_id=run_id)
    controller_resumed.process_mgr.worktree_mgr._run_git = lambda cwd, args: "mutated-sha-000"
    controller_resumed.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "main-head-001", "is_clean": True, "branch": "main"}
    controller_resumed.process_mgr.worktree_mgr.capture_main_baseline = lambda: {"head": "main-head-001", "status": "", "tracked_diff": ""}

    with pytest.raises(RuntimeError, match="Candidate HEAD mismatch on resume"):
        await controller_resumed.resume_from_crash()


@pytest.mark.asyncio
async def test_resume_from_crash_main_baseline_mutation_fails_closed(tmp_path, monkeypatch):
    """Adversarial test: unexpected original MESA changes while controller was dead must fail closed."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    run_id = "run-resume-main-mutated"

    cand_wt = tmp_path / "cand_wt_valid"
    cand_wt.mkdir()

    controller1 = QAController(cfg, run_id=run_id)
    await controller1.controller_db.initialize()
    await controller1.oracle_db.initialize()
    controller1.process_mgr.worktree_mgr._run_git = lambda cwd, args: "head-sha-12345"
    controller1.process_mgr.candidate_worktree = cand_wt
    controller1._main_baseline = {
        "head": "original-mesa-head-111",
        "status": "",
        "tracked_diff": "",
        "toplevel": "/mock/main",
        "common_dir": "/mock/main/.git",
        "untracked_files": "",
    }
    controller1.evidence_store.save_json("main_baseline.json", controller1._main_baseline)
    await controller1._persist_state()

    # Now create controller2 simulating resume after original MESA was changed while controller was dead
    controller2 = QAController(cfg, run_id=run_id)
    controller2.process_mgr.worktree_mgr._run_git = lambda cwd, args: "head-sha-12345"
    controller2.process_mgr.worktree_mgr.check_main_hygiene = lambda: {"head": "unexpected-mesa-mutation-222", "is_clean": False, "branch": "main"}
    # When inspecting original MESA during resume, return changed baseline
    controller2.process_mgr.worktree_mgr.capture_main_baseline = lambda: {
        "head": "unexpected-mesa-mutation-222",
        "status": "M modified_file.py",
        "tracked_diff": "diff --git ...",
        "toplevel": "/mock/main",
        "common_dir": "/mock/main/.git",
        "untracked_files": "",
    }

    with pytest.raises(RuntimeError, match="original MESA checkout changed"):
        await controller2.resume_from_crash()
