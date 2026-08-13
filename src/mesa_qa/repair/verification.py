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
        tests_dir = candidate_worktree / "tests" / "qa_regression"
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_file = tests_dir / f"test_bug_{bug.bug_id.lower().replace('-', '_')}.py"

        test_code = f"""# Autonomous QA Regression Test for {bug.bug_id}
import pytest

def test_regression_{bug.bug_id.lower().replace('-', '_')}():
    # Asserting expected behavior for {bug.category}
    expected = {repr(bug.expected)}
    actual = {repr(bug.actual)}
    assert expected == actual, f"Regression failure for {bug.bug_id}: expected {{expected}}, got {{actual}}"
"""
        test_file.write_text(test_code, encoding="utf-8")
        rel_path = str(test_file.relative_to(candidate_worktree))
        logger.info("Created regression test file at %s", rel_path)
        return rel_path

    def commit_repair(self, candidate_worktree: Path, bug_id: str, summary: str) -> str:
        cmd_add = ["git", "add", "."]
        subprocess.run(cmd_add, cwd=candidate_worktree, check=True)

        msg = f"qa: fix {bug_id} - {summary}"
        cmd_commit = ["git", "commit", "-m", msg]
        subprocess.run(cmd_commit, cwd=candidate_worktree, check=True)

        cmd_sha = ["git", "rev-parse", "HEAD"]
        sha = subprocess.run(cmd_sha, cwd=candidate_worktree, stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
        logger.info("Committed repair for %s: %s (%s)", bug_id, sha, msg)
        return sha
