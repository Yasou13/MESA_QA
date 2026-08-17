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

    def _get_changed_files(self, candidate_worktree: Path) -> List[str]:
        cmd = ["git", "status", "--porcelain=v1", "--untracked-files=all"]
        res = subprocess.run(
            cmd,
            cwd=candidate_worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Failed to run git status: {res.stderr}")

        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                path_part = parts[1]
                if " -> " in path_part:
                    path_part = path_part.split(" -> ")[1]
                files.append(path_part.strip('"'))
        return sorted(list(set(files)))

    def _count_changed_lines(self, candidate_worktree: Path, untracked_files: List[str]) -> int:
        total_lines = 0
        diff_cmd = ["git", "diff", "--numstat"]
        diff_res = subprocess.run(
            diff_cmd,
            cwd=candidate_worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if diff_res.returncode == 0 and diff_res.stdout:
            for line in diff_res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    adds = int(parts[0]) if parts[0].isdigit() else 0
                    dels = int(parts[1]) if parts[1].isdigit() else 0
                    total_lines += adds + dels

        for rel_path in untracked_files:
            file_path = candidate_worktree / rel_path
            if file_path.is_file():
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        total_lines += sum(1 for _ in f)
                except Exception as exc:
                    logger.warning("Could not read untracked file %s for line count: %s", file_path, exc)

        return total_lines

    def validate_diff(self, candidate_worktree: Path) -> Tuple[bool, str]:
        candidate_worktree = candidate_worktree.resolve()

        try:
            files_changed = self._get_changed_files(candidate_worktree)
        except Exception as exc:
            return False, str(exc)

        if len(files_changed) > self.settings.max_auto_changed_files:
            return (
                False,
                f"Patch changed {len(files_changed)} files, exceeding maximum allowed limit of {self.settings.max_auto_changed_files}",
            )

        for fpath in files_changed:
            normalized_path = fpath.replace("\\", "/")
            for forbidden in self.settings.forbidden_repair_paths:
                clean_forbidden = forbidden.rstrip("/")
                if (
                    normalized_path == clean_forbidden
                    or normalized_path.startswith(clean_forbidden + "/")
                    or any(part == clean_forbidden for part in Path(normalized_path).parts)
                ):
                    return False, f"Patch modified forbidden path: '{fpath}'"

        untracked_cmd = ["git", "ls-files", "--others", "--exclude-standard"]
        untracked_res = subprocess.run(
            untracked_cmd,
            cwd=candidate_worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        untracked_files = [f.strip() for f in untracked_res.stdout.splitlines() if f.strip()]

        total_changed_lines = self._count_changed_lines(candidate_worktree, untracked_files)
        if total_changed_lines > self.settings.max_auto_changed_lines:
            return (
                False,
                f"Patch changed {total_changed_lines} lines, exceeding maximum allowed limit of {self.settings.max_auto_changed_lines}",
            )

        logger.info(
            "Repair diff verified: %d files changed, %d lines changed.",
            len(files_changed),
            total_changed_lines,
        )
        return True, "Diff complies with repair safety policy"

    def changed_paths(self, candidate_worktree: Path) -> List[str]:
        return self._get_changed_files(candidate_worktree.resolve())

    def discard_changes(self, candidate_worktree: Path) -> None:
        """Discard uncommitted modifications and untracked files safely from candidate worktree."""
        if not candidate_worktree:
            return
        try:
            candidate_worktree = Path(candidate_worktree).resolve()
            if not candidate_worktree.is_dir():
                return
            logger.info("Discarding uncommitted changes in candidate worktree %s...", candidate_worktree)
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=candidate_worktree, check=False, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=candidate_worktree, check=False, capture_output=True)
        except Exception as exc:
            logger.warning("Could not discard changes in %s: %s", candidate_worktree, exc)
