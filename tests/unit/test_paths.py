import pytest
from pathlib import Path
from mesa_qa.storage.paths import assert_safe_paths

def test_assert_safe_paths_valid(tmp_path):
    main_repo = tmp_path / "MESA"
    main_repo.mkdir()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    storage = tmp_path / "qa_storage"
    storage.mkdir()

    assert_safe_paths(main_repo=main_repo, candidate_worktree=candidate, qa_storage=storage)

def test_assert_safe_paths_equal_candidate_fails(tmp_path):
    main_repo = tmp_path / "MESA"
    main_repo.mkdir()
    storage = tmp_path / "qa_storage"
    storage.mkdir()

    with pytest.raises(ValueError, match="cannot equal main repo path"):
        assert_safe_paths(main_repo=main_repo, candidate_worktree=main_repo, qa_storage=storage)
