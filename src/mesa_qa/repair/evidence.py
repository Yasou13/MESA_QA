from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Dict, List
import logging

from mesa_qa.models import BugReport

logger = logging.getLogger("mesa_qa.evidence")


class EvidenceStore:
    def __init__(self, run_dir: Path):
        self.evidence_dir = (run_dir / "evidence").resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def append_json_record(self, filename: str, record: Dict[str, Any]) -> Path:
        """Append lightweight run evidence without introducing another store."""
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError("evidence filename must be one local .json name")
        path = self.evidence_dir / filename
        records: List[Dict[str, Any]] = []
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise ValueError(f"existing evidence is not a JSON array: {path}")
            records = loaded
        records.append(record)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    def read_json_records(self, filename: str) -> List[Dict[str, Any]]:
        """Read lightweight JSON evidence records."""
        path = self.evidence_dir / filename
        if not path.exists():
            return []
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            return loaded
        return []

    def save_json(self, filename: str, data: Any) -> Path:
        """Save a single JSON evidence object atomically."""
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError("evidence filename must be one local .json name")
        path = self.evidence_dir / filename
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    def read_json(self, filename: str) -> Optional[Any]:
        """Read a single JSON evidence file."""
        path = self.evidence_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def create_bundle(
        self,
        bug: BugReport,
        user_sequence: List[Dict[str, Any]],
        expected_data: Dict[str, Any],
        actual_data: Dict[str, Any],
        repro_execution: Optional[Dict[str, Any]] = None,
    ) -> Path:
        bug_dir = self.evidence_dir / f"repro_{bug.bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)

        # Backward compatibility: link or alias legacy bug.bug_id path
        legacy_dir = self.evidence_dir / bug.bug_id
        if legacy_dir != bug_dir and not legacy_dir.exists():
            try:
                legacy_dir.symlink_to(bug_dir.name)
            except Exception:
                pass

        # 1. bug.json
        (bug_dir / "bug.json").write_text(
            json.dumps(bug.model_dump(), indent=2), encoding="utf-8"
        )

        # 2. user_sequence.jsonl
        with open(bug_dir / "user_sequence.jsonl", "w", encoding="utf-8") as f:
            for item in user_sequence:
                f.write(json.dumps(item) + "\n")

        # 3. expected.json
        (bug_dir / "expected.json").write_text(
            json.dumps(expected_data, indent=2), encoding="utf-8"
        )

        # 4. actual.json
        (bug_dir / "actual.json").write_text(
            json.dumps(actual_data, indent=2), encoding="utf-8"
        )

        # 5. repro_execution.json
        exec_payload = repro_execution or {
            "status": "CONFIRMED_ANOMALY",
            "reproduced": True,
            "strategy": bug.reproduction_strategy,
            "candidate_commit": bug.candidate_commit_before,
        }
        (bug_dir / "repro_execution.json").write_text(
            json.dumps(exec_payload, indent=2), encoding="utf-8"
        )

        # 6. repro.md
        repro_md = f"""# Reproduction Evidence Bundle — {bug.bug_id}

- **Bug ID**: {bug.bug_id}
- **Severity**: {bug.severity.value}
- **Category**: {bug.category}
- **First Seen At**: {bug.first_seen_at}
- **Candidate Commit**: {bug.candidate_commit_before}
- **Reproduction Strategy**: {bug.reproduction_strategy}

## Steps to Reproduce
"""
        for i, step in enumerate(user_sequence, 1):
            repro_md += f"{i}. {json.dumps(step)}\n"

        repro_md += f"""
## Expected Result
```json
{json.dumps(expected_data, indent=2)}
```

## Actual Result
```json
{json.dumps(actual_data, indent=2)}
```
"""
        (bug_dir / "repro.md").write_text(repro_md, encoding="utf-8")

        regression_command = exec_payload.get("regression_command")
        executable_regression = (
            isinstance(regression_command, list)
            and regression_command
            and all(isinstance(part, str) and part for part in regression_command)
        )

        # 7. manifest.json
        manifest = {
            "bug_id": bug.bug_id,
            "run_id": bug.run_id,
            "severity": bug.severity.value,
            "category": bug.category,
            "scenario_id": bug.scenario_id,
            "reproduction_strategy": bug.reproduction_strategy,
            "candidate_commit_before": bug.candidate_commit_before,
            "first_seen_at": bug.first_seen_at,
            "step_count": len(user_sequence),
            "artifacts": [
                "bug.json",
                "user_sequence.jsonl",
                "expected.json",
                "actual.json",
                "repro_execution.json",
                "repro.md",
                "manifest.json",
            ],
        }
        if executable_regression:
            manifest["artifacts"].extend(["reproduce.py", "reproduce.sh"])
        (bug_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        # 8. The replay entrypoints exist only when they execute the generated
        # live-MCP regression. Evidence-only bundles intentionally have no
        # executable entrypoint; printing saved JSON is not reproduction.
        if executable_regression:
            command_json = json.dumps(regression_command)
            reproduce_py = f"""#!/usr/bin/env python3
import json
import subprocess
import sys

COMMAND = json.loads({command_json!r})
raise SystemExit(subprocess.run(COMMAND, check=False).returncode)
"""
            (bug_dir / "reproduce.py").write_text(reproduce_py, encoding="utf-8")
            reproduce_sh = "#!/usr/bin/env bash\nset -euo pipefail\nexec " + " ".join(
                shlex.quote(part) for part in regression_command
            ) + "\n"
            (bug_dir / "reproduce.sh").write_text(reproduce_sh, encoding="utf-8")
            for name in ("reproduce.py", "reproduce.sh"):
                try:
                    (bug_dir / name).chmod(0o755)
                except OSError:
                    logger.warning("Could not make %s executable", bug_dir / name)

        logger.info("Evidence bundle created for %s at %s", bug.bug_id, bug_dir)
        return bug_dir
