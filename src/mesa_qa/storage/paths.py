from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple


def get_user_qa_root() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    qa_root = Path(xdg_data) / "mesa-qa"
    qa_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return qa_root


def get_run_dir(run_id: str, base_dir: Path | None = None) -> Path:
    root = base_dir or get_user_qa_root()
    run_path = root / "runs" / run_id
    run_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    
    # Subdirectories
    (run_path / "mesa-storage").mkdir(exist_ok=True)
    (run_path / "logs").mkdir(exist_ok=True)
    (run_path / "evidence").mkdir(exist_ok=True)
    (run_path / "reports").mkdir(exist_ok=True)
    
    return run_path


def assert_safe_paths(
    main_repo: Path,
    candidate_worktree: Path,
    qa_storage: Path,
) -> None:
    main_resolved = main_repo.resolve()
    candidate_resolved = candidate_worktree.resolve()
    storage_resolved = qa_storage.resolve()

    if main_resolved == candidate_resolved:
        raise ValueError(f"Candidate worktree path cannot equal main repo path: {main_resolved}")

    if storage_resolved == main_resolved or main_resolved in storage_resolved.parents:
        raise ValueError(f"QA storage path cannot be inside main repo: {storage_resolved}")

    if storage_resolved.exists():
        normal_mesa_storage = Path.home() / ".local" / "share" / "mesa"
        if storage_resolved == normal_mesa_storage.resolve():
            raise ValueError(f"QA storage path cannot equal default user MESA storage: {normal_mesa_storage}")


def assert_candidate_isolation(candidate_path: Path, allowed_prefix: str = "qa/autonomous") -> None:
    if not candidate_path.exists():
        raise ValueError(f"Candidate worktree directory does not exist: {candidate_path}")
