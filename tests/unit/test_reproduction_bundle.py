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
    
    bundle_dir = store.create_bundle(
        bug=bug,
        user_sequence=user_seq,
        expected_data=expected_data,
        actual_data=actual_data,
    )
    
    assert bundle_dir.is_dir()
    assert (bundle_dir / "bug.json").exists()
    assert (bundle_dir / "user_sequence.jsonl").exists()
    assert (bundle_dir / "expected.json").exists()
    assert (bundle_dir / "actual.json").exists()
    assert (bundle_dir / "repro.md").exists()
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "reproduce.py").exists()
    assert (bundle_dir / "reproduce.sh").exists()
    
    # Check manifest.json contents
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bug_id"] == "BUG-0001"
    assert manifest["reproduction_strategy"] == "fresh_attempt"
    assert manifest["step_count"] == 2
    assert "reproduce.sh" in manifest["artifacts"]
    
    # Test execution of reproduce.sh
    res = subprocess.run(["bash", str(bundle_dir / "reproduce.sh")], capture_output=True, text=True)
    assert res.returncode == 0
    assert "BUG-0001" in res.stdout
    assert "fresh_attempt" in res.stdout
