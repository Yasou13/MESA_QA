from __future__ import annotations

import asyncio
import pytest

from mesa_qa.cli import _cmd_control
from mesa_qa.storage.controller_db import ControllerDB


@pytest.mark.asyncio
async def test_run_scoped_control_routes_to_target_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    runs_dir = tmp_path / "xdg" / "mesa-qa" / "runs"
    runs_dir.mkdir(parents=True)

    # Create run A
    run_a_dir = runs_dir / "run-A"
    run_a_dir.mkdir()
    db_a = ControllerDB(run_a_dir / "controller.db")
    await db_a.initialize()

    # Create run B
    run_b_dir = runs_dir / "run-B"
    run_b_dir.mkdir()
    db_b = ControllerDB(run_b_dir / "controller.db")
    await db_b.initialize()

    # Request pause for run-A explicitly
    await _cmd_control("pause", run_id="run-A")

    ctrl_a = await db_a.get_control("run-A")
    ctrl_b = await db_b.get_control("run-B")

    assert ctrl_a == "pause"
    assert ctrl_b is None

    # Request resume for run-B explicitly
    await _cmd_control("resume", run_id="run-B")

    ctrl_b2 = await db_b.get_control("run-B")
    assert ctrl_b2 == "resume"


@pytest.mark.asyncio
async def test_run_scoped_control_missing_run_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    with pytest.raises(SystemExit, match="No run found with ID 'run-nonexistent'"):
        await _cmd_control("stop", run_id="run-nonexistent")
