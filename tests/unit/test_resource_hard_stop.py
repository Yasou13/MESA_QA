from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.state_machine import State
from mesa_qa.telemetry.sampler import ResourceSampler


def test_resource_sampler_aggregates_process_tree(tmp_path):
    sampler = ResourceSampler(run_dir=tmp_path, warn_rss_mb=100.0, hard_stop_rss_mb=200.0)

    # Mock psutil Process and children
    mock_root = MagicMock()
    mock_root.memory_info.return_value = MagicMock(rss=120 * 1024 * 1024)
    mock_root.cpu_percent.return_value = 10.0
    mock_root.num_threads.return_value = 4
    mock_root.status.return_value = "running"

    mock_child = MagicMock()
    mock_child.memory_info.return_value = MagicMock(rss=100 * 1024 * 1024)
    mock_child.cpu_percent.return_value = 5.0
    mock_child.num_threads.return_value = 2

    mock_root.children.return_value = [mock_child]

    with patch("psutil.Process", return_value=mock_root):
        metrics = sampler.sample_process_tree(pid=12345)

    assert metrics["pid"] == 12345
    assert metrics["rss_mb"] == 220.0  # 120 + 100
    assert metrics["cpu_percent"] == 15.0  # 10 + 5
    assert metrics["num_threads"] == 6  # 4 + 2
    assert metrics["num_processes"] == 2
    assert metrics["hard_limit_exceeded"] is True

    # Verify log file was written
    assert sampler.log_file.exists()


@pytest.mark.asyncio
async def test_controller_resource_hard_stop_cancels_action_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.run.duration_hours = 1.0
    cfg.resources.sample_seconds = 1  # 1s sampling interval
    cfg.resources.hard_stop_rss_mb = 100.0
    run_id = "run-hard-stop"

    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()
    await controller.oracle_db.initialize()
    controller.state_machine._current_state = State.RUNNING

    controller.scenario_engine.load_suite = lambda: None
    ev = ScenarioEvent(id="ev-long", kind=ActionKind.RECALL, entity="project:atlas", field="backend", expected="FastAPI")
    controller.scenario_engine.next_event = lambda: ev
    controller.scenario_engine.has_next = lambda: True
    controller.process_mgr.stop_all = AsyncMock()

    action_started = asyncio.Event()
    action_cancelled = asyncio.Event()

    async def _mock_process_event(event):
        action_started.set()
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            action_cancelled.set()
            raise

    controller._process_event = _mock_process_event

    # Mock sampler to return hard limit exceeded after short delay
    call_count = 0
    def _mock_sample(pid):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return {"rss_mb": 150.0, "hard_limit_exceeded": True}
        return {"rss_mb": 50.0, "hard_limit_exceeded": False}

    controller.sampler.sample_process_tree = _mock_sample

    loop_task = asyncio.create_task(controller.run_loop())

    await action_started.wait()
    await loop_task

    assert action_cancelled.is_set()
    assert controller.state_machine.current == State.COMPLETED
