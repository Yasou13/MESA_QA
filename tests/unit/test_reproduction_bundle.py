from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from mesa_qa.models import BugReport, Severity
from mesa_qa.repair.evidence import EvidenceStore


def test_reproduction_bundle_artifacts(tmp_path):
    store = EvidenceStore(tmp_path)
    
    bug = BugReport(
        bug_id="BUG-0001",
        run_id="run-repro-test",
        severity=Severity.P1,
        category="data_loss",
        scenario_id="scen-001",
        reproduction_strategy="fresh_attempt",
        candidate_commit_before="abc1234",
    )
    
    user_seq = [
        {"kind": "remember", "text": "fact 1"},
        {"kind": "recall", "text": "query 1"},
    ]
    expected_data = {"facts": ["fact 1"]}
    actual_data = {"facts": []}
    
    repro_exec = {
        "status": "CONFIRMED_ANOMALY",
        "reproduced": True,
        "strategy": "fresh_attempt",
        "candidate_commit": "abc1234",
    }
    
    bundle_dir = store.create_bundle(
        bug=bug,
        user_sequence=user_seq,
        expected_data=expected_data,
        actual_data=actual_data,
        repro_execution=repro_exec,
    )
    
    assert bundle_dir.is_dir()
    assert bundle_dir.name == "repro_BUG-0001"
    assert (bundle_dir / "bug.json").exists()
    assert (bundle_dir / "user_sequence.jsonl").exists()
    assert (bundle_dir / "expected.json").exists()
    assert (bundle_dir / "actual.json").exists()
    assert (bundle_dir / "repro_execution.json").exists()
    assert (bundle_dir / "repro.md").exists()
    assert (bundle_dir / "manifest.json").exists()
    assert not (bundle_dir / "reproduce.py").exists()
    assert not (bundle_dir / "reproduce.sh").exists()
    
    # Check manifest.json contents
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bug_id"] == "BUG-0001"
    assert manifest["reproduction_strategy"] == "fresh_attempt"
    assert manifest["step_count"] == 2
    assert "repro_execution.json" in manifest["artifacts"]
    assert "reproduce.sh" not in manifest["artifacts"]
    
    # Check repro_execution.json contents
    exec_data = json.loads((bundle_dir / "repro_execution.json").read_text(encoding="utf-8"))
    assert exec_data["status"] == "CONFIRMED_ANOMALY"
    assert exec_data["reproduced"] is True
    
    # Evidence-only bundles must not masquerade as executable reproductions.
    assert not (bundle_dir / "reproduce.sh").exists()


def test_reproduction_bundle_runs_a_real_regression_command(tmp_path):
    store = EvidenceStore(tmp_path)
    bug = BugReport(
        bug_id="BUG-0002", run_id="run-repro-test", severity=Severity.P1,
        category="data_loss", scenario_id="scen-002", candidate_commit_before="abc1234",
    )
    bundle_dir = store.create_bundle(
        bug=bug,
        user_sequence=[],
        expected_data={},
        actual_data={},
        repro_execution={"regression_command": ["/bin/true"]},
    )

    res = subprocess.run(["bash", str(bundle_dir / "reproduce.sh")], capture_output=True, text=True)
    assert res.returncode == 0
    assert res.stdout == ""
