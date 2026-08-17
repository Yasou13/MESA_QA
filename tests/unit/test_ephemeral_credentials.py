from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.runtime.candidate_environment import CandidatePythonEnvironment
from mesa_qa.runtime.process_manager import ProcessManager


def test_process_manager_generates_ephemeral_credentials(tmp_path):
    cfg = QAConfig.load()
    pm1 = ProcessManager(cfg, tmp_path / "run1")
    pm2 = ProcessManager(cfg, tmp_path / "run2")

    assert pm1.api_key
    assert pm2.api_key
    assert pm1.api_key != pm2.api_key

    assert pm1.encryption_key
    assert pm2.encryption_key
    assert pm1.encryption_key != pm2.encryption_key

    assert pm1.principal_id
    assert pm2.principal_id
    assert pm1.principal_id != pm2.principal_id
    assert pm1.principal_id.startswith("qa-service-principal-")


@pytest.mark.asyncio
async def test_process_manager_wires_ephemeral_credentials(tmp_path):
    cfg = QAConfig.load()
    run_dir = tmp_path / "run_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    pm = ProcessManager(cfg, run_dir)
    pm.candidate_worktree = tmp_path / "cand_wt"
    pm.candidate_worktree.mkdir(parents=True, exist_ok=True)

    candidate_python = tmp_path / "candidate-runtime" / "venv" / "bin" / "python"
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
        patch("mesa_qa.runtime.process_manager.MesaCandidateRuntime") as mock_rt_cls,
        patch("mesa_qa.runtime.process_manager.MesaMCPGatewayProcess") as mock_gw_cls,
    ):

        mock_rt = AsyncMock()
        mock_rt.wait_until_ready.return_value = True
        mock_rt.base_url = "http://127.0.0.1:18000"
        mock_rt_cls.return_value = mock_rt

        mock_gw = AsyncMock()
        mock_gw.wait_until_ready.return_value = True
        mock_gw_cls.return_value = mock_gw

        await pm.start_all()

        # Check runtime args
        mock_rt_cls.assert_called_once()
        rt_kwargs = mock_rt_cls.call_args.kwargs
        assert rt_kwargs["api_key"] == pm.api_key
        assert rt_kwargs["principal_id"] == pm.principal_id
        assert rt_kwargs["python_bin"] == candidate_python

        # Check gateway args
        mock_gw_cls.assert_called_once()
        gw_kwargs = mock_gw_cls.call_args.kwargs
        assert gw_kwargs["mesa_api_key"] == pm.api_key
        assert gw_kwargs["encryption_key"] == pm.encryption_key
        assert gw_kwargs["python_bin"] == candidate_python
