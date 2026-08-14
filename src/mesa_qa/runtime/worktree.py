from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional
import logging

logger = logging.getLogger("mesa_qa.worktree")


class WorktreeManager:
    def __init__(self, main_repo: Path, candidate_root: Path, branch_prefix: str = "qa/autonomous"):
        self.main_repo = main_repo.resolve()
        self.candidate_root = candidate_root.resolve()
        self.branch_prefix = branch_prefix

    def check_main_hygiene(self) -> Dict[str, str]:
        if not (self.main_repo / ".git").exists():
            raise ValueError(f"Main repository is not a valid Git repository: {self.main_repo}")

        head = self._run_git(self.main_repo, ["rev-parse", "HEAD"]).strip()
        branch = self._run_git(self.main_repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        status = self._run_git(self.main_repo, ["status", "--porcelain"]).strip()

        return {
            "head": head,
            "branch": branch,
            "is_clean": len(status) == 0,
        }

    def capture_main_baseline(self) -> Dict[str, str]:
        """Read-only integrity snapshot; dirty user work is allowed but immutable."""
        return {
            "head": self._run_git(self.main_repo, ["rev-parse", "HEAD"]).strip(),
            "status": self._run_git(self.main_repo, ["status", "--porcelain=v1", "--untracked-files=all"]),
            "tracked_diff": self._run_git(self.main_repo, ["diff", "--binary"]),
        }

    def assert_main_unchanged(self, baseline: Dict[str, str]) -> None:
        current = self.capture_main_baseline()
        if current != baseline:
            raise RuntimeError("P0 safety failure: original MESA checkout changed during MESA-QA lifecycle")

    def create_candidate_worktree(self, run_id: str, baseline_commit: Optional[str] = None) -> Tuple[Path, str]:
        hygiene = self.check_main_hygiene()
        commit = baseline_commit or hygiene["head"]
        branch_name = f"{self.branch_prefix}-{run_id}"
        worktree_path = self.candidate_root / f"run-{run_id}"

        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        if worktree_path.exists():
            logger.warning("Worktree path already exists, cleaning up prior attempt: %s", worktree_path)
            self._run_git(self.main_repo, ["worktree", "remove", "--force", str(worktree_path)], check=False)

        logger.info("Creating Git worktree at %s (branch %s) from commit %s", worktree_path, branch_name, commit)
        self._run_git(self.main_repo, ["worktree", "add", "-B", branch_name, str(worktree_path), commit])

        # Assertions
        candidate_head = self._run_git(worktree_path, ["rev-parse", "HEAD"]).strip()
        if candidate_head != commit:
            raise RuntimeError(f"Candidate HEAD mismatch: expected {commit}, got {candidate_head}")

        if worktree_path.resolve() == self.main_repo.resolve():
            raise RuntimeError("CRITICAL SAFETY FAILURE: Worktree path equals main repo path!")

        return worktree_path.resolve(), branch_name

    def remove_candidate_worktree(self, worktree_path: Path, delete_branch: bool = False, branch_name: Optional[str] = None) -> None:
        path = worktree_path.resolve()
        if path == self.main_repo:
            raise RuntimeError("FORBIDDEN: Attempted to remove main repository directory!")

        if path.exists():
            logger.info("Removing worktree at %s", path)
            self._run_git(self.main_repo, ["worktree", "remove", "--force", str(path)], check=False)

        if delete_branch and branch_name:
            logger.info("Deleting candidate branch %s", branch_name)
            self._run_git(self.main_repo, ["branch", "-D", branch_name], check=False)

    def _run_git(self, cwd: Path, args: list[str], check: bool = True) -> str:
        cmd = ["git"] + args
        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if check and res.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\nStderr: {res.stderr}")
        return res.stdout
