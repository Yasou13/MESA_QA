from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from mesa_qa.repair.verification import RepairVerifier


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.test"], cwd=path, check=True)
    f = path / "README.md"
    f.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)


def test_commit_approved_paths_only(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    verifier = RepairVerifier(python_bin=Path("/usr/bin/python3"))
    
    # Modify an approved file and an unapproved file
    (repo / "fix.py").write_text("def fix(): pass\n", encoding="utf-8")
    (repo / "unapproved.txt").write_text("secrets\n", encoding="utf-8")
    
    sha = verifier.commit_repair(
        candidate_worktree=repo,
        bug_id="BUG-001",
        summary="fix bug",
        approved_paths=["fix.py"],
    )
    assert len(sha) == 40
    
    # Check that only fix.py is in the commit
    diff_out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert diff_out == "fix.py"
    
    # Check that unapproved.txt is still untracked
    status_out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "?? unapproved.txt" in status_out


def test_refuse_empty_approved_paths(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    verifier = RepairVerifier(python_bin=Path("/usr/bin/python3"))
    
    with pytest.raises(RuntimeError, match="Refusing repair commit"):
        verifier.commit_repair(
            candidate_worktree=repo,
            bug_id="BUG-002",
            summary="fix bug",
            approved_paths=[],
        )
