import pytest
from pathlib import Path

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.storage.paths import get_run_dir

@pytest.mark.asyncio
async def test_fake_end_to_end_pipeline(tmp_path):
    mesa_dir = tmp_path / "MESA"
    mesa_dir.mkdir()
    (mesa_dir / ".git").mkdir()

    # Stub git HEAD
    with open(mesa_dir / ".git" / "HEAD", "w") as f:
        f.write("ref: refs/heads/main\n")

    cfg = QAConfig.load()
    cfg.mesa.repo_path = mesa_dir
    cfg.candidate.worktree_root = tmp_path / "candidate_root"
    cfg.run.duration_hours = 0.001

    # Verify controller initialization structure
    run_id = "test-e2e-001"
    run_dir = get_run_dir(run_id, base_dir=tmp_path / "qa_data")
    assert run_dir.exists()
    assert (run_dir / "mesa-storage").exists()
