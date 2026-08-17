from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger("mesa_qa.worktree")


class WorktreeManager:
    def __init__(self, main_repo: Path, candidate_root: Path, branch_prefix: str = "qa/autonomous"):
        self.main_repo = main_repo.resolve()
        self.candidate_root = candidate_root.resolve()
        self.branch_prefix = branch_prefix

    def resolve_ref(self, ref: str) -> str:
        """Resolve branch, tag, or SHA to exact commit SHA read-only without modifying checkout."""
        if not ref or not ref.strip():
            raise ValueError("Candidate ref cannot be empty")
        cleaned_ref = ref.strip()
        try:
            resolved = self._run_git(
                self.main_repo, ["rev-parse", f"{cleaned_ref}^{{commit}}"]
            ).strip()
            if not resolved or len(resolved) != 40:
                raise ValueError(
                    f"Failed to resolve candidate ref to 40-char commit SHA: '{cleaned_ref}'"
                )
            return resolved
        except Exception as exc:
            raise ValueError(
                f"Invalid or unresolvable candidate ref '{cleaned_ref}': {exc}"
            ) from exc

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

    def capture_candidate_snapshot(self, worktree_path: Path) -> Dict[str, Any]:
        """Capture complete pre-repair candidate state and original MESA baseline."""
        wt = Path(worktree_path).resolve()
        untracked_out = self._run_git(wt, ["ls-files", "--others", "--exclude-standard"])
        untracked_set = [f.strip() for f in untracked_out.splitlines() if f.strip()]
        return {
            "candidate_head": self._run_git(wt, ["rev-parse", "HEAD"]).strip(),
            "candidate_branch": self._run_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"]).strip(),
            "candidate_status": self._run_git(wt, ["status", "--porcelain=v1", "--untracked-files=all"]),
            "candidate_tracked_diff": self._run_git(wt, ["diff", "--binary"]),
            "candidate_untracked_files": sorted(untracked_set),
            "main_baseline": self.capture_main_baseline(),
        }

    def assert_main_unchanged(self, baseline: Dict[str, str]) -> None:
        current = self.capture_main_baseline()
        if current != baseline:
            mismatches = {
                k: {"baseline": baseline.get(k), "current": current.get(k)}
                for k in set(baseline) | set(current)
                if baseline.get(k) != current.get(k)
            }
            logger.error("Original MESA checkout changed during QA lifecycle: %s", mismatches)
            raise RuntimeError(f"P0 safety failure: original MESA checkout changed during MESA-QA lifecycle: {mismatches}")

    def validate_candidate_identity(
        self,
        candidate_worktree: Path,
        baseline_commit: Optional[str] = None,
        main_baseline: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, str]:
        wt = Path(candidate_worktree).resolve()

        # 1. Containment check
        try:
            if not wt.is_relative_to(self.candidate_root):
                return False, f"Candidate worktree {wt} is not contained within candidate root {self.candidate_root}"
        except AttributeError:
            if not str(wt).startswith(str(self.candidate_root)):
                return False, f"Candidate worktree {wt} is not contained within candidate root {self.candidate_root}"

        # 2. Main repo collision check
        if wt == self.main_repo:
            return False, "Candidate worktree path equals main repository path"

        # 3. Top-level check
        try:
            toplevel = Path(self._run_git(wt, ["rev-parse", "--show-toplevel"]).strip()).resolve()
            if toplevel != wt:
                return False, f"Candidate worktree top-level {toplevel} does not match expected {wt}"
        except Exception as exc:
            return False, f"Failed to get candidate worktree top-level: {exc}"

        # 4. Common-dir check
        try:
            common_dir = Path(self._run_git(wt, ["rev-parse", "--git-common-dir"]).strip()).resolve()
            main_git = (self.main_repo / ".git").resolve()
            if common_dir != main_git and common_dir != self.main_repo:
                return False, f"Candidate git common-dir {common_dir} does not point to main repo .git {main_git}"
        except Exception as exc:
            return False, f"Failed to get git common-dir: {exc}"

        # 5. Branch prefix check
        try:
            branch = self._run_git(wt, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
            if not branch.startswith(self.branch_prefix):
                return False, f"Candidate branch '{branch}' does not start with required prefix '{self.branch_prefix}'"
            if branch in ("main", "master", "develop", "release"):
                return False, f"Candidate worktree is checked out to forbidden protected branch '{branch}'"
        except Exception as exc:
            return False, f"Failed to get candidate branch: {exc}"

        # 6. Lineage check
        if baseline_commit:
            try:
                res = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", baseline_commit, "HEAD"],
                    cwd=wt,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if res.returncode != 0:
                    return False, f"Candidate HEAD is not a descendant of baseline commit {baseline_commit}"
            except Exception as exc:
                return False, f"Failed to verify candidate lineage: {exc}"

        # 7. Main baseline integrity
        if main_baseline:
            try:
                self.assert_main_unchanged(main_baseline)
            except Exception as exc:
                return False, f"Main repository integrity check failed: {exc}"

        return True, "Candidate identity verified"

    def assert_candidate_identity(
        self,
        candidate_worktree: Path,
        baseline_commit: Optional[str] = None,
        main_baseline: Optional[Dict[str, str]] = None,
    ) -> None:
        ok, reason = self.validate_candidate_identity(
            candidate_worktree, baseline_commit, main_baseline
        )
        if not ok:
            raise RuntimeError(f"Candidate identity hard gate failed: {reason}")

    def create_candidate_worktree(
        self,
        run_id: str,
        candidate_ref: Optional[str] = None,
        baseline_commit: Optional[str] = None,
    ) -> Tuple[Path, str, str]:
        hygiene = self.check_main_hygiene()
        if candidate_ref:
            resolved_commit = self.resolve_ref(candidate_ref)
        elif baseline_commit:
            resolved_commit = self.resolve_ref(baseline_commit)
        else:
            resolved_commit = hygiene["head"]

        branch_name = f"{self.branch_prefix}-{run_id}"
        worktree_path = self.candidate_root / f"run-{run_id}"

        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        if worktree_path.exists():
            raise FileExistsError(f"Candidate worktree path already exists: {worktree_path}")

        res_branch = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=self.main_repo,
            check=False,
        )
        if res_branch.returncode == 0:
            raise FileExistsError(f"Candidate branch already exists: {branch_name}")

        logger.info("Creating Git worktree at %s (branch %s) from commit %s", worktree_path, branch_name, resolved_commit)
        self._run_git(self.main_repo, ["worktree", "add", "-B", branch_name, str(worktree_path), resolved_commit])

        # Assertions
        candidate_head = self._run_git(worktree_path, ["rev-parse", "HEAD"]).strip()
        if candidate_head != resolved_commit:
            raise RuntimeError(f"Candidate HEAD mismatch: expected {resolved_commit}, got {candidate_head}")

        if worktree_path.resolve() == self.main_repo.resolve():
            raise RuntimeError("CRITICAL SAFETY FAILURE: Worktree path equals main repo path!")

        return worktree_path.resolve(), branch_name, resolved_commit

    def remove_candidate_worktree(
        self,
        worktree_path: Path,
        delete_branch: bool = False,
        branch_name: Optional[str] = None,
    ) -> None:
        raw_path = Path(worktree_path)
        if raw_path.is_symlink():
            raise RuntimeError(f"CRITICAL SAFETY VIOLATION: Candidate worktree {raw_path} is a symlink!")

        path = raw_path.resolve()

        # 1. Containment check: must be inside candidate_root
        try:
            path.relative_to(self.candidate_root)
        except ValueError:
            raise RuntimeError(
                f"CRITICAL SAFETY VIOLATION: Worktree path {path} is not contained within candidate root {self.candidate_root}"
            )

        # 2. Main repo, root and home collision check
        if path == self.main_repo:
            raise RuntimeError("FORBIDDEN: Attempted to remove main repository directory!")

        if path == Path("/") or path == Path.home():
            raise RuntimeError("FORBIDDEN: Attempted to remove root or home directory!")

        # 3. Git identity verification (if path exists)
        if path.exists():
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    common_dir = (path / res.stdout.strip()).resolve()
                    expected_main_git = (self.main_repo / ".git").resolve()
                    if common_dir != expected_main_git and common_dir != self.main_repo:
                        raise RuntimeError(
                            f"Git identity mismatch: worktree common dir {common_dir} != main git {expected_main_git}"
                        )
            except Exception as exc:
                if "Git identity mismatch" in str(exc):
                    raise
                logger.warning("Could not verify git common-dir before removal: %s", exc)

            logger.info("Removing worktree at %s", path)
            self._run_git(self.main_repo, ["worktree", "remove", "--force", str(path)], check=False)
            self._run_git(self.main_repo, ["worktree", "prune"], check=False)

        # 4. Safe branch deletion
        if delete_branch and branch_name:
            if branch_name in ("main", "master", "develop", "release", "HEAD"):
                raise RuntimeError(f"FORBIDDEN: Attempted to delete protected branch '{branch_name}'!")
            if not branch_name.startswith(self.branch_prefix):
                raise RuntimeError(
                    f"FORBIDDEN: Branch '{branch_name}' does not match candidate branch prefix '{self.branch_prefix}'"
                )
            logger.info("Deleting candidate branch %s", branch_name)
            self._run_git(self.main_repo, ["branch", "-D", branch_name], check=False)

    def _run_git(self, cwd: Path, args: list[str], check: bool = True) -> str:
        cmd = ["git"] + args
        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if check and res.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\nStderr: {res.stderr}")
        return res.stdout
