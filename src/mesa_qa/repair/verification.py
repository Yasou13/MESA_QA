from __future__ import annotations

import os
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
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        res = subprocess.run(
            cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env
        )
        passed = (res.returncode == 0)
        output = res.stdout + "\n" + res.stderr
        return passed, output

    def verify_pre_fix_failure(self, candidate_worktree: Path, test_file_rel: str) -> Tuple[bool, str]:
        """Verify that a genuine source-path regression exists and demonstrably fails before fix."""
        candidate_worktree = Path(candidate_worktree).resolve()
        target_path = candidate_worktree / test_file_rel
        if not target_path.is_file():
            return False, f"Pre-fix regression file not found: {test_file_rel}"

        content = target_path.read_text(encoding="utf-8")
        if not content.strip():
            return False, f"Pre-fix regression file is empty: {test_file_rel}"

        # Disallow synthetic pseudo-tests
        if "captured_expected == captured_actual" in content or "assert True" == content.strip():
            return False, f"Refusing pseudo-test or pre-generated PASS assertion in {test_file_rel}"

        passed, output = self.run_pytest_on_file(candidate_worktree, test_file_rel)
        if passed:
            return False, f"Test unexpectedly PASSED before fix; genuine PRE-FIX FAIL required.\n{output}"

        logger.info("Genuine PRE-FIX FAIL confirmed for %s", test_file_rel)
        return True, output

    def run_full_suite(self, candidate_worktree: Path) -> Tuple[bool, str]:
        cmd = [str(self.python_bin), "-m", "pytest"]
        logger.info("Running full pytest suite in %s: %s", candidate_worktree, " ".join(cmd))
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        res = subprocess.run(
            cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env
        )
        passed = (res.returncode == 0)
        output = res.stdout + "\n" + res.stderr
        return passed, output

    def run_targeted_tests(self, candidate_worktree: Path, test_paths: List[str]) -> Tuple[bool, str]:
        if not test_paths:
            return True, "No targeted tests to run"
        cmd = [str(self.python_bin), "-m", "pytest", *test_paths]
        logger.info("Running targeted pytest suite in %s: %s", candidate_worktree, " ".join(cmd))
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        res = subprocess.run(
            cmd, cwd=candidate_worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env
        )
        passed = (res.returncode == 0)
        output = res.stdout + "\n" + res.stderr
        return passed, output

    def find_targeted_tests(self, candidate_worktree: Path, changed_files: List[str]) -> List[str]:
        if not candidate_worktree:
            return []
        candidate_worktree = Path(candidate_worktree).resolve()
        targeted = []
        for cf in changed_files:
            p = Path(cf)
            if "test" in p.name.lower() and p.suffix == ".py":
                if (candidate_worktree / cf).is_file():
                    targeted.append(cf)
                continue

            stem = p.stem
            potential_tests = [
                f"tests/test_{stem}.py",
                f"tests/unit/test_{stem}.py",
                f"tests/integration/test_{stem}.py",
                f"test_{stem}.py",
            ]
            for pt in potential_tests:
                if (candidate_worktree / pt).is_file() and pt not in targeted:
                    targeted.append(pt)

        return sorted(list(set(targeted)))

    def create_regression_test(self, candidate_worktree: Path, bug: BugReport) -> str:
        raise RuntimeError(
            "MESA-QA must not synthesize a captured-data regression test; "
            "the reproduction pipeline must supply a real source-path test"
        )

    def commit_repair(
        self,
        candidate_worktree: Path,
        bug_id: str,
        summary: str,
        approved_paths: List[str],
    ) -> str:
        if not approved_paths:
            raise RuntimeError("Refusing repair commit with no approved changed paths")

        candidate_worktree = Path(candidate_worktree).resolve()

        # Reset index to ensure only approved paths will be staged
        subprocess.run(["git", "reset"], cwd=candidate_worktree, capture_output=True, check=False)

        # Stage ONLY approved paths
        cmd_add = ["git", "add", "--", *approved_paths]
        res_add = subprocess.run(cmd_add, cwd=candidate_worktree, capture_output=True, text=True, check=False)
        if res_add.returncode != 0:
            raise RuntimeError(f"Failed to stage approved paths: {res_add.stderr}")

        # Verify staged files strictly belong to approved paths
        res_staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=candidate_worktree,
            capture_output=True,
            text=True,
            check=True,
        )
        staged_files = [f.strip() for f in res_staged.stdout.splitlines() if f.strip()]
        unapproved = [f for f in staged_files if f not in approved_paths]
        if unapproved:
            subprocess.run(["git", "reset"], cwd=candidate_worktree, capture_output=True, check=False)
            raise RuntimeError(f"Security violation: unapproved paths were staged for commit: {unapproved}")

        if not staged_files:
            raise RuntimeError("No changes staged for repair commit")

        msg = f"qa: fix {bug_id} - {summary}"
        cmd_commit = ["git", "commit", "-m", msg]
        res_commit = subprocess.run(cmd_commit, cwd=candidate_worktree, capture_output=True, text=True, check=False)
        if res_commit.returncode != 0:
            raise RuntimeError(f"Failed to commit repair: {res_commit.stderr}")

        cmd_sha = ["git", "rev-parse", "HEAD"]
        sha = subprocess.run(
            cmd_sha, cwd=candidate_worktree, stdout=subprocess.PIPE, text=True, check=True
        ).stdout.strip()
        logger.info("Committed repair for %s: %s (%s)", bug_id, sha, msg)
        return sha
