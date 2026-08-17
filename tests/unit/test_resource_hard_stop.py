from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.state_machine import State
from mesa_qa.telemetry.sampler import ResourceSampler


def test_resource_sampler_aggregates_multiple_owned_trees_without_double_counting(tmp_path):
    sampler = ResourceSampler(run_dir=tmp_path, warn_rss_mb=100.0, hard_stop_rss_mb=200.0)

    # 1. MESA parent process (PID 100)
    proc_100 = MagicMock()
    proc_100.pid = 100
    proc_100.memory_info.return_value = MagicMock(rss=50 * 1024 * 1024)
    proc_100.cpu_percent.return_value = 10.0
    proc_100.num_threads.return_value = 4
    proc_100.status.return_value = "running"

    # MESA child process (PID 101)
    proc_101 = MagicMock()
    proc_101.pid = 101
    proc_101.memory_info.return_value = MagicMock(rss=30 * 1024 * 1024)
    proc_101.cpu_percent.return_value = 5.0
    proc_101.num_threads.return_value = 2
    proc_100.children.return_value = [proc_101]
    proc_101.children.return_value = []

    # 2. MCP Gateway process (PID 200)
    proc_200 = MagicMock()
    proc_200.pid = 200
    proc_200.memory_info.return_value = MagicMock(rss=40 * 1024 * 1024)
    proc_200.cpu_percent.return_value = 2.0
    proc_200.num_threads.return_value = 2
    proc_200.status.return_value = "running"
    proc_200.children.return_value = []

    # 3. Active Codex process (PID 300)
    proc_300 = MagicMock()
    proc_300.pid = 300
    proc_300.memory_info.return_value = MagicMock(rss=100 * 1024 * 1024)
    proc_300.cpu_percent.return_value = 20.0
    proc_300.num_threads.return_value = 8
    proc_300.status.return_value = "running"
    proc_300.children.return_value = []

    proc_lookup = {
        100: proc_100,
        101: proc_101,
        200: proc_200,
        300: proc_300,
    }

    def mock_psutil_proc(pid):
        if pid in proc_lookup:
            return proc_lookup[pid]
        raise ValueError(f"Unknown pid {pid}")

    with patch("psutil.Process", side_effect=mock_psutil_proc):
        # Sample with all owned trees: MESA, Gateway, Codex, plus duplicate child pid 101 to verify deduplication
        metrics = sampler.sample_process_trees([100, 200, 300, 101])

    assert set(metrics["pids"]) == {100, 200, 300, 101}
    assert metrics["num_processes"] == 4
    # Total RSS = 50 + 30 + 40 + 100 = 220 MB
    assert metrics["rss_mb"] == 220.0
    assert metrics["cpu_percent"] == 37.0
    assert metrics["num_threads"] == 16
    assert metrics["hard_limit_exceeded"] is True
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
    def _mock_sample_trees(pids):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return {"rss_mb": 150.0, "num_processes": 3, "hard_limit_exceeded": True}
        return {"rss_mb": 50.0, "num_processes": 2, "hard_limit_exceeded": False}

    controller.sampler.sample_process_trees = _mock_sample_trees

    loop_task = asyncio.create_task(controller.run_loop())

    await action_started.wait()
    await loop_task

    assert action_cancelled.is_set()
    assert controller.state_machine.current == State.COMPLETED

    breach_records = controller.evidence_store.read_json_records("resource_breach.json")
    assert len(breach_records) >= 1
    assert breach_records[0]["rss_mb"] == 150.0
    assert breach_records[0]["hard_stop_rss_mb"] == 100.0
