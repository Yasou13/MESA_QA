import pytest
import json
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


@pytest.mark.asyncio
async def test_mesa_runtime_does_not_override_candidate_embedding_identity():
    """MESA owns embedding dimensions; QA must not force a stale value."""
    runtime = MesaCandidateRuntime(
        candidate_worktree=Path("/tmp/fake_candidate"),
        python_bin=Path("/tmp/fake_python"),
        storage_root=Path("/tmp/fake_storage"),
    )

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc

        await runtime.start()

    env = mock_exec.call_args.kwargs["env"]
    assert "MESA_EMBEDDING_DIMENSION" not in env


@pytest.mark.asyncio
async def test_mesa_runtime_writes_sanitized_launch_lifecycle(tmp_path):
    runtime = MesaCandidateRuntime(
        candidate_worktree=tmp_path / "candidate",
        python_bin=tmp_path / "venv" / "bin" / "python",
        storage_root=tmp_path / "storage",
        api_key="must-not-leak",
        log_file=tmp_path / "logs" / "mesa.log",
    )
    runtime.candidate_worktree.mkdir()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.pid = 777
        mock_proc.returncode = None
        mock_exec.return_value = mock_proc
        await runtime.start()

    evidence = json.loads(runtime.lifecycle_file.read_text(encoding="utf-8"))
    assert evidence["command"] == [
        str((tmp_path / "venv" / "bin" / "python").absolute()),
        "-m",
        "mesa_memory.runtime_entrypoint",
    ]
    assert evidence["cwd"] == str((tmp_path / "candidate").resolve())
    assert evidence["pid"] == 777
    assert evidence["environment"]["MESA_API_KEY"] == "<redacted>"
