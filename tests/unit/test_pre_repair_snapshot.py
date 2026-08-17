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


def test_capture_candidate_snapshot(tmp_path):
    main_repo = tmp_path / "main_repo"
    candidate_root = tmp_path / "candidate_root"
    _init_git_repo(main_repo)
    
    mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt_path, branch, head = mgr.create_candidate_worktree("test-run-snap")
    
    # Add an untracked file in candidate worktree
    (wt_path / "untracked_test.txt").write_text("hello", encoding="utf-8")
    
    # Modify a tracked file
    (wt_path / "README.md").write_text("# Modified\n", encoding="utf-8")
    
    snap = mgr.capture_candidate_snapshot(wt_path)
    assert snap["candidate_head"] == head
    assert snap["candidate_branch"] == branch
    assert "untracked_test.txt" in snap["candidate_untracked_files"]
    assert "README.md" in snap["candidate_status"]
    assert len(snap["candidate_tracked_diff"]) > 0
    assert "main_baseline" in snap
    assert snap["main_baseline"]["head"] == head
    
    # Assert main unchanged succeeds
    mgr.assert_main_unchanged(snap["main_baseline"])
    
    # Mutating main should raise RuntimeError
    (main_repo / "dirty.txt").write_text("bad", encoding="utf-8")
    with pytest.raises(RuntimeError, match="original MESA checkout changed"):
        mgr.assert_main_unchanged(snap["main_baseline"])
