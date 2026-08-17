from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation
from mesa_qa.state_machine import State


@pytest.mark.asyncio
async def test_paused_controller_observes_resume_and_clears_control(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 0.001  # very short duration

    controller = QAController(cfg, run_id="run-pause-resume")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.scenario_engine.load_suite = lambda: None
    controller.scenario_engine.has_next = lambda: False
    controller.process_mgr.stop_all = AsyncMock()

    # Request pause before run_loop
    await controller.controller_db.request_control("run-pause-resume", "pause")

    # Background task to resume after 0.2 seconds
    async def _send_resume():
        await asyncio.sleep(0.2)
        # Ensure pause was processed and cleared
        assert controller.state_machine.current == State.PAUSED
        await controller.controller_db.request_control("run-pause-resume", "resume")

    task = asyncio.create_task(_send_resume())
    await controller.run_loop()
    await task

    # Verify control request was cleared
    ctrl = await controller.controller_db.get_control("run-pause-resume")
    assert ctrl is None
    assert controller.state_machine.current == State.COMPLETED


@pytest.mark.asyncio
async def test_paused_controller_observes_stop_and_clears_control(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 1.0  # long duration

    controller = QAController(cfg, run_id="run-pause-stop")
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.scenario_engine.load_suite = lambda: None
    controller.scenario_engine.has_next = lambda: False
    controller.process_mgr.stop_all = AsyncMock()

    await controller.controller_db.request_control("run-pause-stop", "pause")

    async def _send_stop():
        await asyncio.sleep(0.2)
        assert controller.state_machine.current == State.PAUSED
        await controller.controller_db.request_control("run-pause-stop", "stop")

    task = asyncio.create_task(_send_stop())
    await controller.run_loop()
    await task

    ctrl = await controller.controller_db.get_control("run-pause-stop")
    assert ctrl is None
    assert controller.state_machine.current == State.COMPLETED
