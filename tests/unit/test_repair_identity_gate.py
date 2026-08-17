from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from mesa_qa.runtime.worktree import WorktreeManager


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.test"], cwd=path, check=True)
    f = path / "README.md"
    f.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_valid_candidate_identity_passes(tmp_path):
    main_repo = tmp_path / "main_repo"
    candidate_root = tmp_path / "candidate_root"
    base_sha = _init_git_repo(main_repo)
    
    mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt_path, branch, head = mgr.create_candidate_worktree("identity-pass")
    
    main_baseline = mgr.capture_main_baseline()
    ok, reason = mgr.validate_candidate_identity(
        wt_path, baseline_commit=base_sha, main_baseline=main_baseline
    )
    assert ok, reason
    mgr.assert_candidate_identity(
        wt_path, baseline_commit=base_sha, main_baseline=main_baseline
    )


def test_main_repo_collision_fails(tmp_path):
    main_repo = tmp_path / "main_repo"
    candidate_root = tmp_path / "candidate_root"
    base_sha = _init_git_repo(main_repo)
    
    mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    ok, reason = mgr.validate_candidate_identity(main_repo, baseline_commit=base_sha)
    assert not ok
    assert "not contained" in reason or "equals main" in reason


def test_invalid_branch_prefix_fails(tmp_path):
    main_repo = tmp_path / "main_repo"
    candidate_root = tmp_path / "candidate_root"
    base_sha = _init_git_repo(main_repo)
    
    mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root, branch_prefix="qa/autonomous")
    wt_path, branch, head = mgr.create_candidate_worktree("branch-check")
    
    # Checkout forbidden branch name
    subprocess.run(["git", "checkout", "-b", "feature/unauthorized"], cwd=wt_path, check=True, capture_output=True)
    
    ok, reason = mgr.validate_candidate_identity(wt_path, baseline_commit=base_sha)
    assert not ok
    assert "prefix" in reason


def test_broken_lineage_fails(tmp_path):
    main_repo = tmp_path / "main_repo"
    candidate_root = tmp_path / "candidate_root"
    base_sha = _init_git_repo(main_repo)
    
    mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt_path, branch, head = mgr.create_candidate_worktree("lineage-check")
    
    # Non-existent or unrelated baseline commit
    fake_sha = "0123456789012345678901234567890123456789"
    ok, reason = mgr.validate_candidate_identity(wt_path, baseline_commit=fake_sha)
    assert not ok
    assert "lineage" in reason.lower() or "descendant" in reason.lower()
