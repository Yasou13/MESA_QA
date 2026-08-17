from __future__ import annotations

import json
from pathlib import Path
import pytest

from mesa_qa.telemetry.reports import ReportBuilder


def test_derive_session_verdict_pass(tmp_path):
    builder = ReportBuilder(tmp_path)
    state = {"status": "COMPLETED", "action_count": 50}
    bugs = []
    repairs = []
    assert builder.derive_session_verdict(state, bugs, repairs) == "PASS"


def test_derive_session_verdict_fail(tmp_path):
    builder = ReportBuilder(tmp_path)
    state = {"status": "COMPLETED", "action_count": 50}
    bugs = [{"bug_id": "BUG-01", "status": "CONFIRMED"}]
    repairs = []
    assert builder.derive_session_verdict(state, bugs, repairs) == "FAIL"


def test_derive_session_verdict_blocked_and_not_run(tmp_path):
    builder = ReportBuilder(tmp_path)
    assert builder.derive_session_verdict({"status": "PAUSED", "action_count": 10}, [], []) == "BLOCKED"
    assert builder.derive_session_verdict({"status": "WAITING_FOR_CODEX", "action_count": 10}, [], []) == "BLOCKED"
    assert builder.derive_session_verdict({"status": "INIT", "action_count": 0}, [], []) == "NOT_RUN"


def test_generate_final_report_outputs(tmp_path):
    run_dir = tmp_path / "runs" / "run-rpt-01"
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # Create some mock evidence files
    (run_dir / "controller.log").write_text("log line 1\nlog line 2\n")
    (run_dir / "actions.jsonl").write_text('{"action": "test"}\n')
    repro_dir = run_dir / "reproductions" / "BUG-01"
    repro_dir.mkdir(parents=True, exist_ok=True)

    builder = ReportBuilder(run_dir)
    state = {
        "run_id": "run-rpt-01",
        "status": "COMPLETED",
        "started_at": "2026-08-17T01:00:00Z",
        "action_count": 100,
        "current_epoch": 2,
        "baseline_main_head": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_base_sha": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_branch": "qa/autonomous-run-rpt-01",
        "candidate_head": "06ebd35a5dc43b75c33351705ddf4afa0a732c61",
        "candidate_worktree": "/cand/run-rpt-01",
    }
    bugs = [
        {"bug_id": "BUG-01", "severity": "P1", "category": "data_loss", "status": "VERIFIED", "scenario_id": "ev_01"}
    ]
    repairs = [
        {"bug_id": "BUG-01", "status": "VERIFIED", "commit_sha": "999aaaa"}
    ]

    md_path, json_path = builder.generate_final_report(state, bugs, repairs)
    assert md_path.exists()
    assert json_path.exists()

    report_json = json.loads(json_path.read_text(encoding="utf-8"))
    assert report_json["run_id"] == "run-rpt-01"
    assert report_json["session_verdict"] == "PASS"
    assert report_json["summary"]["total_actions"] == 100
    assert report_json["summary"]["verified_repairs"] == 1
    assert "controller.log" in report_json["evidence_artifacts"]
    assert "BUG-01" in report_json["evidence_artifacts"]["reproduction_bundles"]

    md_content = md_path.read_text(encoding="utf-8")
    assert "## Session Verdict: **PASS**" in md_content
    assert "run-rpt-01" in md_content
    assert "06ebd35a5dc43b75c33351705ddf4afa0a732c61" in md_content
    assert "controller.log" in md_content
