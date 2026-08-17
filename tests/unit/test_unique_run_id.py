from __future__ import annotations

import re
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.storage.paths import generate_run_id, get_run_dir
from mesa_qa.runtime.worktree import WorktreeManager


def test_generate_run_id_format_and_uniqueness():
    id1 = generate_run_id("qa")
    id2 = generate_run_id("qa")

    assert id1 != id2
    pattern = re.compile(r"^qa-\d{8}-\d{6}-[0-9a-f]{8}$")
    assert pattern.match(id1)
    assert pattern.match(id2)


def test_get_run_dir_collision_fails_closed(tmp_path):
    run_id = "test-collision-run"
    dir1 = get_run_dir(run_id, base_dir=tmp_path)
    assert dir1.exists()

    with pytest.raises(FileExistsError, match="Run directory already exists"):
        get_run_dir(run_id, base_dir=tmp_path, fail_if_exists=True)


@pytest.mark.asyncio
async def test_controller_initialize_collision_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    run_id = "run-db-collision"

    controller1 = QAController(QAConfig.load(), run_id=run_id)
    await controller1.controller_db.initialize()
    await controller1._persist_state()

    # Second controller with the same run_id must fail closed upon initialize
    controller2 = QAController(QAConfig.load(), run_id=run_id)
    with pytest.raises(FileExistsError, match="Run ID collision"):
        await controller2.initialize()


def test_worktree_collision_fails_closed(tmp_path, monkeypatch):
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".git").mkdir()

    wt_mgr = WorktreeManager(main_repo=main_repo, candidate_root=tmp_path / "candidates")
    wt_path = tmp_path / "candidates" / "run-colliding-run"
    wt_path.mkdir(parents=True)

    monkeypatch.setattr(wt_mgr, "check_main_hygiene", lambda: {"head": "abc", "is_clean": True, "branch": "main"})
    monkeypatch.setattr(wt_mgr, "resolve_ref", lambda ref: "abc")

    with pytest.raises(FileExistsError, match="Candidate worktree path already exists"):
        wt_mgr.create_candidate_worktree(run_id="colliding-run")
