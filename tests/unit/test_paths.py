import pytest

from mesa_qa.storage.paths import assert_safe_paths, get_run_dir
from mesa_qa.runtime.worktree import WorktreeManager


def test_assert_safe_paths_valid(tmp_path):
    main_repo, candidate, qa_root, normal = (tmp_path / name for name in ("MESA", "candidate", "qa", "mesa-data"))
    for path in (main_repo, candidate, qa_root, normal):
        path.mkdir()
    storage = qa_root / "run" / "mesa-storage"
    storage.mkdir(parents=True)
    assert_safe_paths(main_repo, candidate, storage, normal, qa_root)


@pytest.mark.parametrize("storage_builder", [
    lambda main, normal, qa: normal,
    lambda main, normal, qa: normal / "child",
    lambda main, normal, qa: normal.parent,
    lambda main, normal, qa: main / "mesa-storage",
    lambda main, normal, qa: qa,
])
def test_assert_safe_paths_rejects_overlap(tmp_path, storage_builder):
    main, candidate, normal, qa = (tmp_path / name for name in ("MESA", "candidate", "real-data", "qa"))
    for path in (main, candidate, normal, qa):
        path.mkdir()
    storage = storage_builder(main, normal, qa)
    storage.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="overlap|contained"):
        assert_safe_paths(main, candidate, storage, normal, qa)


def test_assert_safe_paths_rejects_storage_symlink(tmp_path):
    main, candidate, normal, qa, other = (tmp_path / name for name in ("MESA", "candidate", "real", "qa", "other"))
    for path in (main, candidate, normal, qa, other):
        path.mkdir()
    storage = qa / "mesa-storage"
    storage.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        assert_safe_paths(main, candidate, storage, normal, qa)


@pytest.mark.parametrize("run_id", ["../escape", "../../escape", "/absolute", "has/slash", ".", "..", ""])
def test_get_run_dir_rejects_unsafe_ids(tmp_path, run_id):
    with pytest.raises(ValueError):
        get_run_dir(run_id, tmp_path / "qa")


def test_get_run_dir_rejects_preexisting_storage_symlink(tmp_path):
    root = tmp_path / "qa"
    run = root / "runs" / "safe-run"
    run.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    (run / "mesa-storage").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        get_run_dir("safe-run", root)


def test_main_integrity_snapshot_allows_dirty_baseline_then_detects_change(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "qa@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("one")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    (tmp_path / "user-note.txt").write_text("existing")
    manager = WorktreeManager(tmp_path, tmp_path / "candidates")
    baseline = manager.capture_main_baseline()
    manager.assert_main_unchanged(baseline)
    (tmp_path / "another-user-note.txt").write_text("changed")
    with pytest.raises(RuntimeError, match="P0 safety failure"):
        manager.assert_main_unchanged(baseline)
