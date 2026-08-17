import subprocess
from pathlib import Path
import pytest

from mesa_qa.config import QAConfig, MesaSettings
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.runtime.process_manager import ProcessManager


@pytest.fixture
def multi_commit_repo(tmp_path: Path):
    repo_dir = tmp_path / "multi_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)

    # Commit 1 on main
    (repo_dir / "base.txt").write_text("v1")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1 commit"], cwd=repo_dir, check=True, capture_output=True)
    sha_1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "tag", "release-1.0"], cwd=repo_dir, check=True, capture_output=True)

    # Commit 2 on branch candidate-a
    subprocess.run(["git", "checkout", "-b", "candidate-a"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "candidate_a.txt").write_text("feature a")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "candidate a commit"], cwd=repo_dir, check=True, capture_output=True)
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Commit 3 on branch candidate-b (branched from main)
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "candidate-b"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "candidate_b.txt").write_text("feature b")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "candidate b commit"], cwd=repo_dir, check=True, capture_output=True)
    sha_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Switch back to main
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

    return {
        "repo_dir": repo_dir,
        "sha_1": sha_1,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "candidate_root": tmp_path / "candidates",
        "run_dir": tmp_path / "run",
    }


def test_two_different_candidate_refs_produce_different_candidate_identities(multi_commit_repo):
    repo_dir = multi_commit_repo["repo_dir"]
    cand_root = multi_commit_repo["candidate_root"]
    run_dir = multi_commit_repo["run_dir"]
    run_dir.mkdir(parents=True, exist_ok=True)

    # Config for Candidate A
    cfg_a = QAConfig(
        mesa=MesaSettings(
            repo_path=repo_dir,
            candidate_ref="candidate-a",
            normal_storage_root=repo_dir / "storage",
        ),
    )
    cfg_a.candidate.worktree_root = cand_root

    pm_a = ProcessManager(config=cfg_a, run_dir=run_dir / "run_a")
    wt_a = pm_a.setup_worktree(run_id="run_a")
    assert pm_a.candidate_base_sha == multi_commit_repo["sha_a"]
    head_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_a, check=True, capture_output=True, text=True).stdout.strip()
    assert head_a == multi_commit_repo["sha_a"]

    # Config for Candidate B
    cfg_b = QAConfig(
        mesa=MesaSettings(
            repo_path=repo_dir,
            candidate_ref="candidate-b",
            normal_storage_root=repo_dir / "storage",
        ),
    )
    cfg_b.candidate.worktree_root = cand_root

    pm_b = ProcessManager(config=cfg_b, run_dir=run_dir / "run_b")
    wt_b = pm_b.setup_worktree(run_id="run_b")
    assert pm_b.candidate_base_sha == multi_commit_repo["sha_b"]
    head_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_b, check=True, capture_output=True, text=True).stdout.strip()
    assert head_b == multi_commit_repo["sha_b"]

    # Assert that candidate A and B produce distinct, non-hardcoded candidate identities
    assert pm_a.candidate_base_sha != pm_b.candidate_base_sha
    assert head_a != head_b
    assert head_a == multi_commit_repo["sha_a"]
    assert head_b == multi_commit_repo["sha_b"]

    # Teardown
    pm_a.teardown(delete_worktree=True)
    pm_b.teardown(delete_worktree=True)
