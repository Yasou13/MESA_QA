import pytest
from pathlib import Path
import asyncio

from mesa_qa.config import QAConfig
from mesa_qa.runtime.process_manager import ProcessManager
from mesa_qa.runtime.health import check_mesa_health
from mesa_qa.storage.paths import get_run_dir, assert_safe_paths

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

    pm = ProcessManager(config=cfg, run_dir=run_dir)
    wt = pm.setup_worktree(run_id=run_id)
    assert wt.exists()
    assert wt != mesa_repo.resolve()

    try:
        await pm.start_all()
        assert pm.mesa_runtime is not None
        assert pm.mcp_gateway is not None

        # Verify MESA Candidate Health
        health = await check_mesa_health(pm.mesa_runtime.base_url, api_key=pm.mesa_runtime.api_key)
        assert health["status"] == "healthy"

    finally:
        await pm.stop_all()
        pm.teardown(delete_worktree=True)
