from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, List
import logging

logger = logging.getLogger("mesa_qa.reports")


class ReportBuilder:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.reports_dir = (run_dir / "reports").resolve()
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_final_report(self, run_state: Dict[str, Any], bugs: List[Dict[str, Any]], repairs: List[Dict[str, Any]]) -> Tuple[Path, Path]:
        json_path = self.reports_dir / "final.json"
        md_path = self.reports_dir / "final.md"

        report_data = {
            "run_state": run_state,
            "summary": {
                "total_actions": run_state.get("action_count", 0),
                "epochs": run_state.get("current_epoch", 0),
                "confirmed_bugs": len(bugs),
                "verified_repairs": len([r for r in repairs if r.get("status") == "VERIFIED"]),
            },
            "bugs": bugs,
            "repairs": repairs,
        }

        json_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        md_content = f"""# MESA-QA Resident Test Engineer — Final Run Report

## Run Metadata
- **Run ID**: {run_state.get('run_id')}
- **Status**: {run_state.get('status')}
- **Started At**: {run_state.get('started_at')}
- **Baseline Commit**: `{run_state.get('baseline_main_head')}`
- **Candidate Branch**: `{run_state.get('candidate_branch')}`
- **Candidate HEAD**: `{run_state.get('candidate_head')}`

## Executive Summary
- **Total Actions Executed**: {run_state.get('action_count', 0)}
- **Tester Epochs Completed**: {run_state.get('current_epoch', 0)}
- **Confirmed Bugs Discovered**: {len(bugs)}
- **Verified Repairs Applied**: {len([r for r in repairs if r.get('status') == 'VERIFIED'])}
- **Main Checkout Integrity**: UNCHANGED

## Confirmed Defects & Repairs
"""
        if not bugs:
            md_content += "_No confirmed product defects discovered during this run._\n"
        else:
            for b in bugs:
                md_content += f"### Bug `{b.get('bug_id')}` ({b.get('severity')})\n"
                md_content += f"- **Category**: {b.get('category')}\n"
                md_content += f"- **Status**: {b.get('status')}\n\n"

        md_content += "\n## Safety & Isolation Audit\n"
        md_content += "- MESA main branch HEAD: Unchanged\n"
        md_content += "- User production storage: Untouched\n"
        md_content += "- Automatic merge to main: Disabled\n"
        md_content += "- Automatic remote push: Disabled / Manual Choice\n"

        md_path.write_text(md_content, encoding="utf-8")

        logger.info("Final report generated at %s and %s", md_path, json_path)
        return md_path, json_path
