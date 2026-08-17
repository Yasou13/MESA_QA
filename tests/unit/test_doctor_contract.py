from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mesa_qa.cli import run_doctor_checks
from mesa_qa.config import QAConfig


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA Doctor"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "doc@mesa.test"], cwd=path, check=True)
    f = path / "README.md"
    f.write_text("# Doctor Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True)


def test_doctor_contract_success(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("MESA_NORMAL_STORAGE_ROOT", str(tmp_path / "mesa_normal_storage"))
    repo = tmp_path / "MESA"
    _init_git_repo(repo)

    # Fake venv python and mesa cli
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    py_bin = venv_bin / "python"
    py_bin.write_text("#!/bin/sh\nexit 0\n")
    py_bin.chmod(0o755)
    mesa_cli = venv_bin / "mesa"
    mesa_cli.write_text("#!/bin/sh\nexit 0\n")
    mesa_cli.chmod(0o755)

    orig_run = subprocess.run

    def selective_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and "codex" in str(cmd[0]):
            res = MagicMock()
            res.returncode = 0
            res.stdout = "codex-cli 1.0.0"
            res.stderr = ""
            return res
        return orig_run(cmd, *args, **kwargs)

    with patch("shutil.which", return_value="/usr/bin/codex"), \
         patch("subprocess.run", side_effect=selective_run):

        success, passes, issues = run_doctor_checks(mesa_repo=repo)
        assert issues == []
        assert success is True
        assert any("MESA repository verified" in p for p in passes)
        assert any("Codex auth type" in p for p in passes)
        assert any("Paid-provider fallback policy: strictly disabled" in p for p in passes)
        assert any("MESA validation mode" in p for p in passes)


def test_doctor_detects_missing_repo(tmp_path):
    missing_repo = tmp_path / "nonexistent"
    success, passes, issues = run_doctor_checks(mesa_repo=missing_repo)
    assert success is False
    assert any("does not exist" in iss for iss in issues)


def test_doctor_detects_candidate_root_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "MESA"
    _init_git_repo(repo)

    cfg_file = tmp_path / "qa_config.yaml"
    cfg_file.write_text(f"""
mesa:
  repo_path: "{repo}"
candidate:
  worktree_root: "{repo}"
""", encoding="utf-8")

    success, passes, issues = run_doctor_checks(config_path=cfg_file, mesa_repo=repo)
    assert success is False
    assert any("Candidate root cannot equal MESA main checkout" in iss for iss in issues)


def test_doctor_detects_missing_codex_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "MESA"
    _init_git_repo(repo)

    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python").write_text("#!/bin/sh\nexit 0\n")
    (venv_bin / "python").chmod(0o755)
    (venv_bin / "mesa").write_text("#!/bin/sh\nexit 0\n")
    (venv_bin / "mesa").chmod(0o755)

    with patch("shutil.which", return_value=None):
        success, passes, issues = run_doctor_checks(mesa_repo=repo)
        assert success is False
        assert any("Codex CLI executable" in iss for iss in issues)


def test_doctor_detects_missing_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "MESA"
    _init_git_repo(repo)

    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    # Python script that fails when -m pytest is requested
    (venv_bin / "python").write_text("""#!/usr/bin/env python3
import sys
if "-m" in sys.argv and "pytest" in sys.argv:
    sys.exit(1)
sys.exit(0)
""")
    (venv_bin / "python").chmod(0o755)
    (venv_bin / "mesa").write_text("#!/bin/sh\nexit 0\n")
    (venv_bin / "mesa").chmod(0o755)

    with patch("shutil.which", return_value="/usr/bin/codex"):
        success, passes, issues = run_doctor_checks(mesa_repo=repo)
        assert success is False
        assert any("pytest is missing" in iss for iss in issues)

