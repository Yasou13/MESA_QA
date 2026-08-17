from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from mesa_qa.config import SafetySettings
from mesa_qa.repair.policy import RepairPolicyGuard


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.test"], cwd=path, check=True)
    f = path / "README.md"
    f.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)


def test_valid_small_patch(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    settings = SafetySettings(max_auto_changed_files=5, max_auto_changed_lines=50)
    guard = RepairPolicyGuard(settings)
    
    (repo / "README.md").write_text("# Updated\n", encoding="utf-8")
    ok, reason = guard.validate_diff(repo)
    assert ok, reason


def test_exceeding_max_files(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    settings = SafetySettings(max_auto_changed_files=2, max_auto_changed_lines=100)
    guard = RepairPolicyGuard(settings)
    
    (repo / "f1.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "f2.py").write_text("b = 2\n", encoding="utf-8")
    (repo / "f3.py").write_text("c = 3\n", encoding="utf-8")
    
    ok, reason = guard.validate_diff(repo)
    assert not ok
    assert "exceeding maximum allowed limit" in reason


def test_exceeding_max_lines(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    settings = SafetySettings(max_auto_changed_files=5, max_auto_changed_lines=10)
    guard = RepairPolicyGuard(settings)
    
    # Add an untracked file with 25 lines
    (repo / "big_file.py").write_text("line\n" * 25, encoding="utf-8")
    
    ok, reason = guard.validate_diff(repo)
    assert not ok
    assert "25 lines" in reason or "exceeding maximum allowed limit" in reason


def test_forbidden_path_tracked(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    settings = SafetySettings(forbidden_repair_paths=[".github/", "uv.lock", "pyproject.toml"])
    guard = RepairPolicyGuard(settings)
    
    (repo / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True)
    
    ok, reason = guard.validate_diff(repo)
    assert not ok
    assert "forbidden path" in reason


def test_forbidden_path_untracked(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    
    settings = SafetySettings(forbidden_repair_paths=[".github/", "deploy/"])
    guard = RepairPolicyGuard(settings)
    
    gh_dir = repo / ".github" / "workflows"
    gh_dir.mkdir(parents=True, exist_ok=True)
    (gh_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    
    ok, reason = guard.validate_diff(repo)
    assert not ok
    assert "forbidden path" in reason
    assert ".github" in reason
