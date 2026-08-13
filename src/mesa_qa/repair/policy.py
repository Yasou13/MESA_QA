from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple
import logging

from mesa_qa.config import SafetySettings

logger = logging.getLogger("mesa_qa.repair_policy")


class RepairPolicyGuard:
    def __init__(self, safety_settings: SafetySettings):
        self.settings = safety_settings

    def validate_diff(self, candidate_worktree: Path) -> Tuple[bool, str]:
        candidate_worktree = candidate_worktree.resolve()

        # Check git status for changed files
        cmd = ["git", "status", "--porcelain"]
        res = subprocess.run(cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            return False, f"Failed to run git status: {res.stderr}"

        changed_lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        files_changed = [line.split()[-1] for line in changed_lines]

        if len(files_changed) > self.settings.max_auto_changed_files:
            return False, f"Patch changed {len(files_changed)} files, exceeding maximum allowed limit of {self.settings.max_auto_changed_files}"

        for fpath in files_changed:
            for forbidden in self.settings.forbidden_repair_paths:
                if fpath.startswith(forbidden) or fpath == forbidden.rstrip("/"):
                    return False, f"Patch modified forbidden path: '{fpath}'"

        # Check line count diff
        diff_cmd = ["git", "diff", "--stat"]
        diff_res = subprocess.run(diff_cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if diff_res.returncode == 0 and diff_res.stdout:
            stat_line = diff_res.stdout.splitlines()[-1] if diff_res.stdout.splitlines() else ""
            logger.info("Git diff stat: %s", stat_line)

        return True, "Diff complies with repair safety policy"
