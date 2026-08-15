from __future__ import annotations

import json
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

    def create_bundle(
        self,
        bug: BugReport,
        user_sequence: List[Dict[str, Any]],
        expected_data: Dict[str, Any],
        actual_data: Dict[str, Any],
    ) -> Path:
        bug_dir = self.evidence_dir / bug.bug_id
        bug_dir.mkdir(parents=True, exist_ok=True)

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

        # 5. repro.md
        repro_md = f"""# Reproduction Evidence Bundle — {bug.bug_id}

- **Bug ID**: {bug.bug_id}
- **Severity**: {bug.severity.value}
- **Category**: {bug.category}
- **First Seen At**: {bug.first_seen_at}
- **Candidate Commit**: {bug.candidate_commit_before}

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

        logger.info("Evidence bundle created for %s at %s", bug.bug_id, bug_dir)
        return bug_dir
