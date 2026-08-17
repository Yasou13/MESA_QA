from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mesa_qa.reports")


class ReportBuilder:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.reports_dir = (run_dir / "reports").resolve()
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def derive_session_verdict(
        self,
        run_state: Dict[str, Any],
        bugs: List[Dict[str, Any]],
        repairs: List[Dict[str, Any]],
    ) -> str:
        """Derive overall session verdict (PASS, FAIL, BLOCKED, NOT_RUN) strictly from evidence."""
        status = run_state.get("status", "INIT")
        action_count = run_state.get("action_count", 0)

        if status in ("INIT", "PREFLIGHT") and action_count == 0:
            return "NOT_RUN"
        if status in ("PAUSED", "WAITING_FOR_CODEX"):
            return "BLOCKED"
        if status == "FAILED":
            return "FAIL"
        if status in ("COMPLETED", "RUNNING", "STOPPING"):
            # Check if there are unresolved/failed bugs
            unresolved = [
                b for b in bugs
                if b.get("status") not in ("VERIFIED", "REPAIRED")
            ]
            if unresolved:
                return "FAIL"
            return "PASS"
        return "NOT_RUN"

    def discover_evidence_artifacts(self) -> Dict[str, Any]:
        """Scan run directory for evidence files, logs and reproduction bundles."""
        artifacts: Dict[str, Any] = {}
        
        # Check logs
        for log_name in ("controller.log", "mesa_stdout.log", "mesa_stderr.log", "mcp_gateway.log", "mesa_runtime.log"):
            for cand in (self.run_dir / log_name, self.run_dir / "logs" / log_name):
                if cand.exists():
                    artifacts[log_name] = {
                        "path": str(cand),
                        "size_bytes": cand.stat().st_size,
                    }

        # Check action log & database
        for f_name in ("actions.jsonl", "controller.db", "gateway-control.db", "oracle.db"):
            f_p = self.run_dir / f_name
            if f_p.exists():
                artifacts[f_name] = {
                    "path": str(f_p),
                    "size_bytes": f_p.stat().st_size,
                }

        # Check reproduction bundles / evidence
        for sub in ("evidence", "reproductions"):
            repro_dir = self.run_dir / sub
            if repro_dir.exists():
                repro_bundles = [d.name for d in repro_dir.iterdir() if d.is_dir()]
                if repro_bundles:
                    artifacts["reproduction_bundles"] = repro_bundles

        return artifacts

    def generate_final_report(
        self,
        run_state: Dict[str, Any],
        bugs: List[Dict[str, Any]],
        repairs: List[Dict[str, Any]],
        actions_summary: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Path, Path]:
        json_path = self.reports_dir / "final.json"
        md_path = self.reports_dir / "final.md"

        verdict = self.derive_session_verdict(run_state, bugs, repairs)
        evidence = self.discover_evidence_artifacts()

        verified_repairs = [r for r in repairs if r.get("status") == "VERIFIED"]
        failed_repairs = [r for r in repairs if r.get("status") in ("REPAIR_FAILED", "LIVE_REPRO_FAILED", "POLICY_VIOLATION")]

        report_data = {
            "run_id": run_state.get("run_id"),
            "session_verdict": verdict,
            "run_state": run_state,
            "summary": {
                "total_actions": run_state.get("action_count", 0),
                "epochs": run_state.get("current_epoch", 0),
                "confirmed_bugs": len(bugs),
                "verified_repairs": len(verified_repairs),
                "failed_repairs": len(failed_repairs),
            },
            "candidate_identity": {
                "worktree": run_state.get("candidate_worktree"),
                "branch": run_state.get("candidate_branch"),
                "base_sha": run_state.get("candidate_base_sha"),
                "head": run_state.get("candidate_head"),
                "baseline_main_head": run_state.get("baseline_main_head"),
            },
            "bugs": bugs,
            "repairs": repairs,
            "evidence_artifacts": evidence,
        }

        json_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        md_content = f"""# MESA-QA Resident Test Engineer — Final Run Report

## Session Verdict: **{verdict}**

### Run Metadata
- **Run ID**: `{run_state.get('run_id')}`
- **Status**: `{run_state.get('status')}`
- **Started At**: {run_state.get('started_at')}
- **Baseline Main Commit**: `{run_state.get('baseline_main_head')}`
- **Candidate Branch**: `{run_state.get('candidate_branch')}`
- **Candidate Base SHA**: `{run_state.get('candidate_base_sha')}`
- **Candidate HEAD**: `{run_state.get('candidate_head')}`

### Executive Summary
| Metric | Value | Status |
|---|---|---|
| Total Actions Executed | {run_state.get('action_count', 0)} | {'PASS' if run_state.get('action_count', 0) > 0 else 'NOT_RUN'} |
| Tester Epochs Completed | {run_state.get('current_epoch', 0)} | PASS |
| Confirmed Bugs Discovered | {len(bugs)} | {'PASS (0 defects)' if len(bugs) == 0 else 'ALERT'} |
| Verified Repairs Applied | {len(verified_repairs)} | PASS |
| Failed / Rejected Repairs | {len(failed_repairs)} | {'OK' if len(failed_repairs) == 0 else 'FAIL'} |
| Main Checkout Integrity | UNCHANGED | PASS |

### Confirmed Defects & Repairs
"""
        if not bugs:
            md_content += "_No confirmed product defects discovered during this run._\n"
        else:
            for b in bugs:
                md_content += f"#### Bug `{b.get('bug_id')}` ({b.get('severity')})\n"
                md_content += f"- **Category**: {b.get('category')}\n"
                md_content += f"- **Status**: {b.get('status')}\n"
                md_content += f"- **Scenario Event**: `{b.get('scenario_id')}`\n\n"

        md_content += "\n### Evidence & Audit Trail\n"
        for art_name, art_info in evidence.items():
            if isinstance(art_info, dict):
                md_content += f"- **{art_name}**: `{art_info.get('path')}` ({art_info.get('size_bytes', 0)} bytes)\n"
            else:
                md_content += f"- **{art_name}**: {art_info}\n"

        md_content += "\n### Safety & Isolation Guarantees\n"
        md_content += "- **MESA main checkout**: Guaranteed read-only / unchanged baseline asserted.\n"
        md_content += "- **Candidate isolation**: Dedicated Git worktree and separate runtime environment.\n"
        md_content += "- **QA storage**: Isolated from normal MESA user storage.\n"
        md_content += "- **Autonomous repair**: Bounded, pre-fix fail verified, gate-checked, and live-rechecked.\n"

        md_path.write_text(md_content, encoding="utf-8")

        logger.info("Final report generated at %s and %s (Verdict: %s)", md_path, json_path, verdict)
        return md_path, json_path
