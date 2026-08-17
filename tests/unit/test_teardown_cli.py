from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
import pytest

from mesa_qa.cli import _async_cmd_teardown, _cmd_teardown
from mesa_qa.config import QAConfig
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.storage.controller_db import ControllerDB


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "QA Tester"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.test"], cwd=path, capture_output=True, check=True)
    (path / "README.md").write_text("# Main MESA\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.asyncio
async def test_teardown_cli_full_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    main_repo = tmp_path / "main_mesa"
    candidate_root = tmp_path / "candidate_root"
    base_sha = _init_git_repo(main_repo)

    run_id = "qa-teardown-test-01"
    wt_mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt_path, branch, head = wt_mgr.create_candidate_worktree(run_id)

    # Setup run dir with controller.db, lock file, and evidence
    qa_root = tmp_path / "xdg" / "mesa-qa"
    run_dir = qa_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "test.lock").write_text("locked", encoding="utf-8")
    (run_dir / "ipc.sock").write_text("socket", encoding="utf-8")

    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "audit.json").write_text('{"status": "PASS"}', encoding="utf-8")

    db = ControllerDB(run_dir / "controller.db")
    await db.initialize()
    await db.save_run_state({
        "run_id": run_id,
        "status": "RUNNING",
        "action_count": 10,
        "mesa_pid": 99999,
        "mcp_gateway_pid": 99998,
    })

    # Save custom config
    cfg_file = tmp_path / "qa_config.yaml"
    cfg = QAConfig()
    cfg.mesa.repo_path = main_repo
    cfg.candidate.worktree_root = candidate_root
    cfg_file.write_text(f"""
mesa:
  repo_path: "{main_repo}"
candidate:
  worktree_root: "{candidate_root}"
""", encoding="utf-8")

    args = argparse.Namespace(
        run_id=run_id,
        config=cfg_file,
        mesa_repo=main_repo,
    )

    # Execute teardown
    await _async_cmd_teardown(args)

    # 1. Candidate worktree is removed
    assert not wt_path.exists()

    # 2. Lock and socket files cleaned
    assert not (run_dir / "test.lock").exists()
    assert not (run_dir / "ipc.sock").exists()

    # 3. Audit records and DB preserved
    assert (run_dir / "controller.db").exists()
    assert (evidence_dir / "audit.json").exists()

    # 4. Main MESA repo is completely untouched and clean
    assert main_repo.exists()
    assert (main_repo / "README.md").read_text(encoding="utf-8") == "# Main MESA\n"
    res_status = subprocess.run(["git", "status", "--porcelain"], cwd=main_repo, capture_output=True, text=True, check=True)
    assert res_status.stdout.strip() == ""


@pytest.mark.asyncio
async def test_teardown_all_runs_when_no_run_id_specified(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    main_repo = tmp_path / "main_mesa"
    candidate_root = tmp_path / "candidate_root"
    _init_git_repo(main_repo)

    wt_mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt1, _, _ = wt_mgr.create_candidate_worktree("run-alpha")
    wt2, _, _ = wt_mgr.create_candidate_worktree("run-beta")

    qa_root = tmp_path / "xdg" / "mesa-qa"
    for r in ("run-alpha", "run-beta"):
        rd = qa_root / "runs" / r
        rd.mkdir(parents=True, exist_ok=True)
        db = ControllerDB(rd / "controller.db")
        await db.initialize()
        await db.save_run_state({"run_id": r, "status": "RUNNING"})

    cfg_file = tmp_path / "qa_config.yaml"
    cfg_file.write_text(f"""
mesa:
  repo_path: "{main_repo}"
candidate:
  worktree_root: "{candidate_root}"
""", encoding="utf-8")

    args = argparse.Namespace(
        run_id=None,
        config=cfg_file,
        mesa_repo=main_repo,
    )

    await _async_cmd_teardown(args)

    assert not wt1.exists()
    assert not wt2.exists()
    assert (qa_root / "runs" / "run-alpha" / "controller.db").exists()
    assert (qa_root / "runs" / "run-beta" / "controller.db").exists()
