from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.runtime.candidate_environment import CandidatePythonEnvironment
from mesa_qa.runtime.process_manager import ProcessManager


@pytest.mark.asyncio
async def test_restart_all_preserves_isolated_storage(tmp_path):
    run_dir = tmp_path / "runs" / "run-restart"
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = QAConfig.load()
    cfg.mesa.repo_path = tmp_path / "main_repo"
    cfg.mesa.repo_path.mkdir(parents=True, exist_ok=True)
    cfg.mesa.normal_storage_root = tmp_path / "normal_storage"
    cfg.mesa.normal_storage_root.mkdir(parents=True, exist_ok=True)
    cfg.candidate.worktree_root = tmp_path / "candidate_root"
    cfg.candidate.worktree_root.mkdir(parents=True, exist_ok=True)

    pm = ProcessManager(config=cfg, run_dir=run_dir)
    pm.candidate_worktree = cfg.candidate.worktree_root / "run-restart"
    pm.candidate_worktree.mkdir(parents=True, exist_ok=True)

    mock_mesa = MagicMock()
    mock_mesa.start = AsyncMock()
    mock_mesa.stop = AsyncMock()
    mock_mesa.wait_until_ready = AsyncMock(return_value=True)
    mock_mesa.base_url = "http://localhost:8000"

    mock_mcp = MagicMock()
    mock_mcp.start = AsyncMock()
    mock_mcp.stop = AsyncMock()
    mock_mcp.wait_until_ready = AsyncMock(return_value=True)

    pm.mesa_runtime = mock_mesa
    pm.mcp_gateway = mock_mcp
    candidate_python = run_dir / "candidate-runtime" / "venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text("", encoding="utf-8")
    environment = CandidatePythonEnvironment(
        base_python=candidate_python,
        python_bin=candidate_python,
        environment_root=candidate_python.parent.parent,
        version="3.12.0",
    )

    with (
        patch(
            "mesa_qa.runtime.process_manager.CandidateEnvironmentManager.prepare",
            return_value=environment,
        ),
        patch(
            "mesa_qa.runtime.process_manager.MesaCandidateRuntime",
            return_value=mock_mesa,
        ) as mock_runtime_cls,
        patch(
            "mesa_qa.runtime.process_manager.MesaMCPGatewayProcess",
            return_value=mock_mcp,
        ),
    ):

        await pm.restart_all()

        # Verify stop was called on both services
        mock_mcp.stop.assert_called_once()
        mock_mesa.stop.assert_called_once()

        # Verify start was called on both services
        assert mock_mesa.start.call_count == 1
        assert mock_mcp.start.call_count == 1

        # Verify storage_root passed to MesaCandidateRuntime is the isolated qa_storage
        _, kwargs = mock_runtime_cls.call_args
        assert kwargs["storage_root"] == run_dir / "mesa-storage"
        assert kwargs["candidate_worktree"] == pm.candidate_worktree


@pytest.mark.asyncio
async def test_restart_all_fails_if_worktree_missing(tmp_path):
    run_dir = tmp_path / "runs" / "run-none"
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = QAConfig.load()
    pm = ProcessManager(config=cfg, run_dir=run_dir)
    pm.candidate_worktree = None

    with pytest.raises(RuntimeError, match="Candidate worktree is not set up"):
        await pm.restart_all()
