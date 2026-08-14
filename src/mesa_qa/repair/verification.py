from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple
import logging

from mesa_qa.models import BugReport, RepairResult

logger = logging.getLogger("mesa_qa.repair_verification")


class RepairVerifier:
    def __init__(self, python_bin: Path):
        self.python_bin = Path(python_bin).absolute()

    def run_pytest_on_file(self, candidate_worktree: Path, test_file_rel: str) -> Tuple[bool, str]:
        cmd = [str(self.python_bin), "-m", "pytest", test_file_rel]
        logger.info("Running pytest in %s: %s", candidate_worktree, " ".join(cmd))
        res = subprocess.run(
            cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
        )
        passed = (res.returncode == 0)
        output = res.stdout + "\n" + res.stderr
        return passed, output

    def create_regression_test(self, candidate_worktree: Path, bug: BugReport) -> str:
        raise RuntimeError(
            "MESA-QA must not synthesize a captured-data regression test; "
            "the reproduction pipeline must supply a real source-path test"
        )

    def commit_repair(self, candidate_worktree: Path, bug_id: str, summary: str, approved_paths: List[str]) -> str:
        if not approved_paths:
            raise RuntimeError("Refusing repair commit with no approved changed paths")
        cmd_add = ["git", "add", "--", *approved_paths]
        subprocess.run(cmd_add, cwd=candidate_worktree, check=True)

        msg = f"qa: fix {bug_id} - {summary}"
        cmd_commit = ["git", "commit", "-m", msg]
        subprocess.run(cmd_commit, cwd=candidate_worktree, check=True)

        cmd_sha = ["git", "rev-parse", "HEAD"]
        sha = subprocess.run(cmd_sha, cwd=candidate_worktree, stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
        logger.info("Committed repair for %s: %s (%s)", bug_id, sha, msg)
        return sha
