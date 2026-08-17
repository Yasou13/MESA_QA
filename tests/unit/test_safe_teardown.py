from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.runtime.process_manager import ProcessManager
from mesa_qa.runtime.worktree import WorktreeManager


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.test"], cwd=path, check=True)
    f = path / "README.md"
    f.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)


def test_teardown_rejects_symlink(tmp_path):
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = tmp_path / "cand_root"
    cand_root.mkdir(parents=True, exist_ok=True)
    
    wm = WorktreeManager(main_repo=main_repo, candidate_root=cand_root)
    
    # Create symlink pointing to candidate_root
    target = cand_root / "real_dir"
    target.mkdir()
    symlink_path = cand_root / "symlink_dir"
    symlink_path.symlink_to(target)
    
    with pytest.raises(RuntimeError, match="is a symlink"):
        wm.remove_candidate_worktree(symlink_path)


def test_teardown_rejects_uncontained_path(tmp_path):
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = tmp_path / "cand_root"
    cand_root.mkdir(parents=True, exist_ok=True)
    
    wm = WorktreeManager(main_repo=main_repo, candidate_root=cand_root)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    
    with pytest.raises(RuntimeError, match="not contained within candidate root"):
        wm.remove_candidate_worktree(outside)


def test_teardown_rejects_main_repo_or_root(tmp_path):
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = main_repo  # dangerous setup
    
    wm = WorktreeManager(main_repo=main_repo, candidate_root=cand_root)
    with pytest.raises(RuntimeError, match="Attempted to remove main repository"):
        wm.remove_candidate_worktree(main_repo)


def test_teardown_rejects_protected_branch_deletion(tmp_path):
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = tmp_path / "cand_root"
    cand_root.mkdir(parents=True, exist_ok=True)
    
    wm = WorktreeManager(main_repo=main_repo, candidate_root=cand_root)
    wt_path = cand_root / "run-001"
    
    with pytest.raises(RuntimeError, match="Attempted to delete protected branch"):
        wm.remove_candidate_worktree(wt_path, delete_branch=True, branch_name="main")


def test_teardown_rejects_unprefixed_branch_deletion(tmp_path):
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = tmp_path / "cand_root"
    cand_root.mkdir(parents=True, exist_ok=True)
    
    wm = WorktreeManager(main_repo=main_repo, candidate_root=cand_root, branch_prefix="qa/autonomous")
    wt_path = cand_root / "run-002"
    
    with pytest.raises(RuntimeError, match="does not match candidate branch prefix"):
        wm.remove_candidate_worktree(wt_path, delete_branch=True, branch_name="feature/user-work")


@pytest.mark.asyncio
async def test_process_manager_async_teardown(tmp_path):
    run_dir = tmp_path / "runs" / "run-td"
    run_dir.mkdir(parents=True, exist_ok=True)
    main_repo = tmp_path / "main_repo"
    _init_git_repo(main_repo)
    cand_root = tmp_path / "cand_root"
    cand_root.mkdir(parents=True, exist_ok=True)
    
    cfg = QAConfig.load()
    cfg.mesa.repo_path = main_repo
    cfg.candidate.worktree_root = cand_root
    
    pm = ProcessManager(config=cfg, run_dir=run_dir)
    wt_path, branch, base_sha = pm.worktree_mgr.create_candidate_worktree("td-01")
    pm.candidate_worktree = wt_path
    pm.candidate_branch = branch
    
    mock_mesa = MagicMock()
    mock_mesa.stop = AsyncMock()
    mock_mcp = MagicMock()
    mock_mcp.stop = AsyncMock()
    
    pm.mesa_runtime = mock_mesa
    pm.mcp_gateway = mock_mcp
    
    assert wt_path.exists()
    
    await pm.async_teardown(delete_worktree=True, delete_branch=True)
    
    mock_mcp.stop.assert_called_once()
    mock_mesa.stop.assert_called_once()
    assert pm.candidate_worktree is None
    assert not wt_path.exists()
