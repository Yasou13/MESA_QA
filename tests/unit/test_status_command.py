from __future__ import annotations

import json
from pathlib import Path
import pytest

from mesa_qa.cli import _format_status_report
from mesa_qa.storage.controller_db import ControllerDB


@pytest.mark.asyncio
async def test_controller_db_get_full_status(tmp_path):
    db_path = tmp_path / "controller.db"
    db = ControllerDB(db_path)
    await db.initialize()

    run_id = "run-status-01"
    state_payload = {
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": "2026-08-17T01:00:00Z",
        "planned_end_at": "2026-08-17T09:00:00Z",
        "baseline_main_head": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_base_sha": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_branch": "qa/autonomous-run-status-01",
        "candidate_head": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_worktree": "/tmp/cand/run-status-01",
        "qa_storage_root": "/tmp/qa-storage",
        "current_epoch": 1,
        "action_count": 45,
        "confirmed_bug_count": 1,
        "verified_repair_count": 0,
        "scenario_cursor": 12,
        "scenario_seed": 42,
        "tester_thread_id": "thread-s01",
        "mesa_pid": 1111,
        "mcp_gateway_pid": 2222,
    }
    await db.save_run_state(state_payload)

    # Record actions
    await db.record_action(
        action_id="act-1",
        run_id=run_id,
        scenario_event_id="ev_01",
        action_type="REMEMBER",
        request={"text": "fact 1"},
        response={"status": "stored"},
        verdict="PASS",
        executed_at="2026-08-17T01:05:00Z",
    )
    await db.record_action(
        action_id="act-2",
        run_id=run_id,
        scenario_event_id="ev_02",
        action_type="RECALL",
        request={"query": "fact 1"},
        response={"answer": "fact 1"},
        verdict="PASS",
        executed_at="2026-08-17T01:06:00Z",
    )

    # Record bug
    await db.record_bug(
        bug_id="BUG-001",
        run_id=run_id,
        severity="P2",
        category="recall_error",
        bug_data={"msg": "wrong answer"},
        status="CONFIRMED",
        created_at="2026-08-17T01:07:00Z",
    )

    full_status = await db.get_full_status(run_id)
    assert full_status is not None
    assert full_status["run_id"] == run_id
    assert full_status["status"] == "RUNNING"
    assert full_status["candidate_identity"]["branch"] == "qa/autonomous-run-status-01"
    assert full_status["candidate_identity"]["base_sha"] == "06ebd35a5dc43b75c33351705ddf4afa0a732c61"
    assert full_status["pids"]["mesa_pid"] == 1111
    assert full_status["pids"]["mcp_gateway_pid"] == 2222
    assert full_status["active_action"]["action_count"] == 45
    assert full_status["active_action"]["scenario_cursor"] == 12
    assert full_status["last_action"]["action_id"] == "act-2"
    assert full_status["last_action"]["action_type"] == "RECALL"
    assert full_status["blocker"] is None
    assert full_status["bugs"]["confirmed"] == 1
    assert full_status["bugs"]["total"] == 1


@pytest.mark.asyncio
async def test_full_status_reports_control_blocker(tmp_path):
    db_path = tmp_path / "controller.db"
    db = ControllerDB(db_path)
    await db.initialize()

    run_id = "run-status-02"
    await db.save_run_state({"run_id": run_id, "status": "PAUSED", "started_at": "2026-08-17T01:00:00Z"})
    await db.request_control(run_id, "resume")

    full_status = await db.get_full_status(run_id)
    assert full_status is not None
    assert full_status["blocker"] == "Control requested: resume"


def test_format_status_report():
    status_data = {
        "run_id": "run-xyz",
        "status": "RUNNING",
        "started_at": "2026-08-17T01:00:00Z",
        "last_updated_at": "2026-08-17T01:10:00Z",
        "candidate_identity": {
            "worktree": "/cand/run-xyz",
            "branch": "qa/autonomous-run-xyz",
            "base_sha": "abc1234",
            "head": "abc1234",
            "baseline_main_head": "abc1234",
        },
        "pids": {
            "mesa_pid": 5001,
            "mcp_gateway_pid": 5002,
        },
        "active_action": {
            "current_epoch": 2,
            "scenario_cursor": 15,
            "action_count": 80,
            "tester_thread_id": "thread-beta",
        },
        "last_action": {
            "action_id": "act-80",
            "scenario_event_id": "ev_80",
            "action_type": "FORGET",
            "verdict": "PASS",
            "executed_at": "2026-08-17T01:09:50Z",
        },
        "blocker": None,
        "bugs": {
            "total": 0,
            "confirmed": 0,
            "verified": 0,
        },
    }

    report = _format_status_report(status_data)
    assert "=== MESA-QA Run Status ===" in report
    assert "Run ID:               run-xyz" in report
    assert "MESA Runtime PID:     5001" in report
    assert "MCP Gateway PID:      5002" in report
    assert "Worktree:             /cand/run-xyz" in report
    assert "Branch:               qa/autonomous-run-xyz" in report
    assert "Action ID:            act-80" in report
    assert "Blocker:              None" in report
