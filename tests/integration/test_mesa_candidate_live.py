import pytest
from pathlib import Path

from mesa_qa.config import QAConfig
from mesa_qa.runtime.process_manager import ProcessManager
from mesa_qa.runtime.health import check_mesa_health
from mesa_qa.storage.paths import get_run_dir


@pytest.mark.asyncio
async def test_live_mesa_candidate_startup():
    mesa_repo = Path("/home/yasin/Desktop/MESA")
    if not mesa_repo.exists() or not (mesa_repo / ".git").exists():
        pytest.skip("Target MESA repository not available")

    import time

    run_id = f"test-live-{int(time.time())}"
    run_dir = get_run_dir(run_id)

    cfg = QAConfig.load()
    cfg.mesa.repo_path = mesa_repo
    cfg.mesa.port = 19001
    cfg.mesa.gateway_port = 19765
    cfg.candidate.worktree_root = Path("/home/yasin/Desktop/MESA-QA-candidate")
    cfg.candidate.python_path = Path(
        "/home/yasin/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12"
    )
    if not cfg.candidate.python_path.is_file():
        pytest.skip("Supported Python 3.12 candidate interpreter is not available")
    original_env = mesa_repo / ".venv"
    original_env_stat = original_env.stat() if original_env.exists() else None

    pm = ProcessManager(config=cfg, run_dir=run_dir)
    wt = pm.setup_worktree(run_id=run_id)
    assert wt.exists()
    assert wt != mesa_repo.resolve()

    try:
        await pm.start_all()
        assert pm.mesa_runtime is not None
        assert pm.mcp_gateway is not None
        assert pm.candidate_python is not None
        assert pm.candidate_python.is_relative_to(run_dir / "candidate-runtime")
        assert pm.candidate_environment is not None
        assert pm.candidate_environment.version.startswith("3.12.")

        # Verify MESA Candidate Health
        health = await check_mesa_health(
            pm.mesa_runtime.base_url, api_key=pm.mesa_runtime.api_key
        )
        assert health["status"] == "healthy"

    finally:
        await pm.stop_all()
        pm.teardown(delete_worktree=True)

    if original_env_stat is not None:
        assert original_env.stat().st_mtime_ns == original_env_stat.st_mtime_ns
