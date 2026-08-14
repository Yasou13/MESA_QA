from __future__ import annotations

import os
import re
from pathlib import Path


RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+\Z")


def _canonical(path: Path) -> Path:
    """Return a canonical identity without requiring the final component to exist."""
    return path.expanduser().resolve(strict=False)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _overlaps(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink: {path}")


def get_user_qa_root() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    qa_root = Path(xdg_data).expanduser() / "mesa-qa"
    _reject_symlink(qa_root, "QA root")
    qa_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return _canonical(qa_root)


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Run ID must match [A-Za-z0-9._-]+ and contain no path separators")
    if run_id in {".", ".."}:
        raise ValueError("Run ID cannot be a traversal component")
    return run_id


def get_run_dir(run_id: str, base_dir: Path | None = None) -> Path:
    """Create and return a non-symlink QA run directory contained by its root."""
    validate_run_id(run_id)
    root = _canonical(base_dir) if base_dir is not None else get_user_qa_root()
    _reject_symlink(root, "QA root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runs_root = root / "runs"
    _reject_symlink(runs_root, "QA runs root")
    runs_root.mkdir(exist_ok=True, mode=0o700)
    run_path = runs_root / run_id
    _reject_symlink(run_path, "QA run root")
    run_path.mkdir(exist_ok=True, mode=0o700)
    resolved_run = _canonical(run_path)
    resolved_root = _canonical(root)
    if not _is_within(resolved_run, resolved_root):
        raise ValueError(f"QA run path escapes QA root: {resolved_run}")
    for name in ("mesa-storage", "logs", "evidence", "reports"):
        child = run_path / name
        _reject_symlink(child, f"QA {name}")
        child.mkdir(exist_ok=True, mode=0o700)
        if not _is_within(_canonical(child), resolved_run):
            raise ValueError(f"QA {name} escapes its run root: {child}")
    return resolved_run


def discover_normal_mesa_storage(main_repo: Path) -> Path:
    """Find MESA's configured normal storage without loading its dotenv file."""
    configured = os.environ.get("MESA_NORMAL_STORAGE_ROOT") or os.environ.get("MESA_STORAGE_ROOT")
    if not configured:
        dotenv = main_repo / ".env"
        if dotenv.is_file():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                if line.startswith("MESA_STORAGE_ROOT="):
                    configured = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not configured:
        raise ValueError("Normal MESA storage is not configured; set mesa.normal_storage_root or MESA_NORMAL_STORAGE_ROOT")
    return _canonical(Path(configured))


def assert_safe_paths(
    main_repo: Path,
    candidate_worktree: Path,
    qa_storage: Path,
    normal_mesa_storage: Path | None = None,
    qa_root: Path | None = None,
) -> None:
    """Fail closed unless all QA-owned paths are canonical, contained and disjoint."""
    for path, label in ((main_repo, "Main MESA repository"), (candidate_worktree, "Candidate worktree"), (qa_storage, "QA storage")):
        _reject_symlink(path, label)
    main_resolved = _canonical(main_repo)
    candidate_resolved = _canonical(candidate_worktree)
    storage_resolved = _canonical(qa_storage)
    normal_storage_resolved = _canonical(normal_mesa_storage) if normal_mesa_storage else discover_normal_mesa_storage(main_resolved)
    if main_resolved == candidate_resolved or _overlaps(main_resolved, candidate_resolved):
        raise ValueError("Candidate worktree must not overlap main MESA repository")
    if _overlaps(storage_resolved, main_resolved):
        raise ValueError("QA storage must not overlap main MESA repository")
    if _overlaps(storage_resolved, normal_storage_resolved):
        raise ValueError("QA storage must not overlap normal MESA storage")
    if qa_root is not None:
        qa_root_resolved = _canonical(qa_root)
        if storage_resolved == qa_root_resolved or not _is_within(storage_resolved, qa_root_resolved):
            raise ValueError("QA storage must be contained by the QA root")


def assert_candidate_isolation(candidate_path: Path, allowed_prefix: str = "qa/autonomous") -> None:
    _reject_symlink(candidate_path, "Candidate worktree")
    if not candidate_path.exists():
        raise ValueError(f"Candidate worktree directory does not exist: {candidate_path}")
