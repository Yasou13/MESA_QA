import subprocess
from pathlib import Path
import pytest

from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.config import QAConfig
from mesa_qa.runtime.process_manager import ProcessManager


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    repo_dir = tmp_path / "origin_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)

    # Initial commit (c1)
    (repo_dir / "file1.txt").write_text("commit 1")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)
    c1_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Tag commit 1
    subprocess.run(["git", "tag", "v1.0.0"], cwd=repo_dir, check=True, capture_output=True)

    # Create feature branch (feature/test) with commit 2 (c2)
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "file2.txt").write_text("commit 2")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Feature commit"], cwd=repo_dir, check=True, capture_output=True)
    c2_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Switch back to main
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

    return {
        "repo_dir": repo_dir,
        "c1_sha": c1_sha,
        "c2_sha": c2_sha,
        "tag": "v1.0.0",
        "branch": "feature/test",
        "candidate_root": tmp_path / "candidates",
    }


def test_resolve_ref_branch(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    sha = wm.resolve_ref(temp_git_repo["branch"])
    assert sha == temp_git_repo["c2_sha"]


def test_resolve_ref_tag(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    sha = wm.resolve_ref(temp_git_repo["tag"])
    assert sha == temp_git_repo["c1_sha"]


def test_resolve_ref_full_sha(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    sha = wm.resolve_ref(temp_git_repo["c2_sha"])
    assert sha == temp_git_repo["c2_sha"]


def test_resolve_ref_short_sha(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    short_sha = temp_git_repo["c2_sha"][:7]
    sha = wm.resolve_ref(short_sha)
    assert sha == temp_git_repo["c2_sha"]


def test_resolve_ref_invalid_raises(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    with pytest.raises(ValueError, match="Invalid or unresolvable candidate ref"):
        wm.resolve_ref("nonexistent-branch-or-tag")


def test_resolve_ref_empty_raises(temp_git_repo):
    wm = WorktreeManager(main_repo=temp_git_repo["repo_dir"], candidate_root=temp_git_repo["candidate_root"])
    with pytest.raises(ValueError, match="Candidate ref cannot be empty"):
        wm.resolve_ref("   ")


def test_create_worktree_from_candidate_ref_preserves_main_checkout(temp_git_repo):
    main_repo = temp_git_repo["repo_dir"]
    candidate_root = temp_git_repo["candidate_root"]
    wm = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)

    # Initial state of main
    baseline = wm.capture_main_baseline()
    assert baseline["head"] == temp_git_repo["c1_sha"]

    # Create candidate worktree from feature branch (c2)
    wt_path, branch, resolved_sha = wm.create_candidate_worktree(
        run_id="test1", candidate_ref="feature/test"
    )

    assert wt_path.exists()
    assert resolved_sha == temp_git_repo["c2_sha"]

    # Candidate HEAD equals c2_sha
    candidate_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert candidate_head == temp_git_repo["c2_sha"]

    # Main repo HEAD and status remain completely unchanged
    wm.assert_main_unchanged(baseline)
    main_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=main_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert main_head_after == temp_git_repo["c1_sha"]

    # Cleanup worktree
    wm.remove_candidate_worktree(wt_path, delete_branch=True, branch_name=branch)
    wm.assert_main_unchanged(baseline)
