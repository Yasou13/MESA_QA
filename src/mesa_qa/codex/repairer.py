from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
import logging

from mesa_qa.codex.runner import CodexRunner
from mesa_qa.models import BugReport, RepairResult

logger = logging.getLogger("mesa_qa.repairer")


class RepairerCodex:
    def __init__(
        self,
        runner: CodexRunner,
        prompts_dir: Path,
        python_bin: Path,
        timeout_seconds: int = 1200,
    ):
        self.runner = runner
        self.prompts_dir = prompts_dir.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.timeout_seconds = timeout_seconds

    async def execute_repair(
        self,
        bug: BugReport,
        candidate_worktree: Path,
        evidence_summary: str,
    ) -> RepairResult:
        candidate_worktree = candidate_worktree.resolve()

        repair_file = self.prompts_dir / "REPAIR.md"
        template = repair_file.read_text(encoding="utf-8") if repair_file.exists() else "{evidence_summary}"

        prompt = template.format(evidence_summary=evidence_summary)

        logger.info("Executing Repair Codex for bug %s in worktree %s...", bug.bug_id, candidate_worktree)

        res = await self.runner.run(
            prompt=prompt,
            cwd=candidate_worktree,
            sandbox="workspace-write",
            timeout_seconds=self.timeout_seconds,
        )

        return self._parse_repair_result(res.output_text, res.raw_stdout, bug.bug_id)

    def _parse_repair_result(self, output_text: str, raw_stdout: str, bug_id: str) -> RepairResult:
        for text in (output_text, raw_stdout):
            if not text:
                continue
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict) and "bug_id" in data:
                            return RepairResult.model_validate(data)
                    except Exception:
                        pass
        return RepairResult(
            bug_id=bug_id,
            success=False,
            error_message="Could not parse Repair Codex output JSON",
        )
