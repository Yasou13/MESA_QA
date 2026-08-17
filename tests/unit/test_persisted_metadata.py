from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_started_at_is_immutable_across_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-meta-immut")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()

    original_started_at = controller._started_at
    assert original_started_at is not None

    # Persist state initial
    await controller._persist_state()
    state1 = await controller.controller_db.get_run_state("run-meta-immut")
    assert state1["started_at"] == original_started_at

    # Small delay to ensure timestamp would differ if recomputed
    await asyncio.sleep(0.05)

    # Persist state again multiple times
    controller.state_machine._current_state = State.RUNNING
    await controller._set_state(State.RUNNING)
    state2 = await controller.controller_db.get_run_state("run-meta-immut")
    assert state2["started_at"] == original_started_at

    await controller._set_state(State.PAUSED)
    state3 = await controller.controller_db.get_run_state("run-meta-immut")
    assert state3["started_at"] == original_started_at


@pytest.mark.asyncio
async def test_candidate_metadata_stored_in_distinct_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    controller = QAController(QAConfig.load(), run_id="run-meta-fields")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()

    # Simulate setup worktree metadata
    controller.process_mgr.candidate_base_sha = "1111222233334444555566667777888899990000"
    controller.process_mgr.candidate_branch = "candidate/run-meta-fields"
    controller.process_mgr.candidate_worktree = tmp_path / "cand_wt"

    await controller._persist_state()

    state = await controller.controller_db.get_run_state("run-meta-fields")
    assert state is not None
    assert state["candidate_base_sha"] == "1111222233334444555566667777888899990000"
    assert state["candidate_branch"] == "candidate/run-meta-fields"
    assert state["candidate_worktree"] == str(tmp_path / "cand_wt")
