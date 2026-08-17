import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mesa_qa.runtime.mesa_runtime import MesaCandidateRuntime


@pytest.mark.asyncio
async def test_mesa_runtime_propagates_mode_0_deterministic():
    runtime = MesaCandidateRuntime(
        candidate_worktree=Path("/tmp/fake_candidate"),
        python_bin=Path("/tmp/fake_python"),
        storage_root=Path("/tmp/fake_storage"),
        validation_mode=0,
        external_provider_enabled=False,
    )

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc

        await runtime.start()

        assert mock_exec.called
        call_kwargs = mock_exec.call_args.kwargs
        env = call_kwargs["env"]

        assert env["MESA_TIER3_MODE"] == "0"
        assert env["MESA_EXTERNAL_PROVIDER_ENABLED"] == "false"
        assert "MESA_TIER3_LLM_PROVIDER_A" not in env
        assert "MESA_TIER3_LLM_PROVIDER_B" not in env


@pytest.mark.asyncio
async def test_mesa_runtime_propagates_mode_1_single_llm():
    runtime = MesaCandidateRuntime(
        candidate_worktree=Path("/tmp/fake_candidate"),
        python_bin=Path("/tmp/fake_python"),
        storage_root=Path("/tmp/fake_storage"),
        validation_mode=1,
        llm_provider="mock",
    )

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc

        await runtime.start()

        assert mock_exec.called
        call_kwargs = mock_exec.call_args.kwargs
        env = call_kwargs["env"]

        assert env["MESA_TIER3_MODE"] == "1"
        assert env["MESA_TIER3_LLM_PROVIDER_A"] == "mock"
        assert env["MESA_TIER3_LLM_MODEL_A"] == "mesa-qa-validator-a"
        assert "MESA_TIER3_LLM_PROVIDER_B" not in env


@pytest.mark.asyncio
async def test_mesa_runtime_propagates_mode_2_dual_llm():
    runtime = MesaCandidateRuntime(
        candidate_worktree=Path("/tmp/fake_candidate"),
        python_bin=Path("/tmp/fake_python"),
        storage_root=Path("/tmp/fake_storage"),
        validation_mode=2,
        llm_provider="mock",
    )

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc

        await runtime.start()

        assert mock_exec.called
        call_kwargs = mock_exec.call_args.kwargs
        env = call_kwargs["env"]

        assert env["MESA_TIER3_MODE"] == "2"
        assert env["MESA_TIER3_LLM_PROVIDER_A"] == "mock"
        assert env["MESA_TIER3_LLM_MODEL_A"] == "mesa-qa-validator-a"
        assert env["MESA_TIER3_LLM_PROVIDER_B"] == "mock"
        assert env["MESA_TIER3_LLM_MODEL_B"] == "mesa-qa-validator-b"


@pytest.mark.asyncio
async def test_mesa_runtime_unset_mode():
    runtime = MesaCandidateRuntime(
        candidate_worktree=Path("/tmp/fake_candidate"),
        python_bin=Path("/tmp/fake_python"),
        storage_root=Path("/tmp/fake_storage"),
        validation_mode=None,
    )

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc

        await runtime.start()

        assert mock_exec.called
        call_kwargs = mock_exec.call_args.kwargs
        env = call_kwargs["env"]

        assert "MESA_TIER3_MODE" not in env
