from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from mesa_qa.runtime.mesa_runtime import MesaCandidateRuntime
from mesa_qa.runtime.mcp_gateway import MesaMCPGatewayProcess


@pytest.mark.asyncio
async def test_mesa_runtime_closes_parent_log_handle(tmp_path):
    log_file = tmp_path / "logs" / "mesa.log"
    runtime = MesaCandidateRuntime(
        candidate_worktree=tmp_path / "wt",
        python_bin=tmp_path / "python",
        storage_root=tmp_path / "storage",
        log_file=log_file,
    )

    opened_files = []
    orig_open = open

    def tracking_open(file, *args, **kwargs):
        f = orig_open(file, *args, **kwargs)
        if str(file) == str(log_file):
            opened_files.append(f)
        return f

    with patch("builtins.open", side_effect=tracking_open), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        
        proc = AsyncMock()
        proc.pid = 1234
        proc.returncode = None
        mock_exec.return_value = proc

        await runtime.start()

        assert len(opened_files) == 1
        # Check that parent file handle was closed
        assert opened_files[0].closed is True


@pytest.mark.asyncio
async def test_mcp_gateway_closes_parent_log_handle(tmp_path):
    log_file = tmp_path / "logs" / "mcp_gw.log"
    gateway = MesaMCPGatewayProcess(
        candidate_worktree=tmp_path / "wt",
        python_bin=tmp_path / "python",
        control_db_path=tmp_path / "gw.db",
        log_file=log_file,
    )

    opened_files = []
    orig_open = open

    def tracking_open(file, *args, **kwargs):
        f = orig_open(file, *args, **kwargs)
        if str(file) == str(log_file):
            opened_files.append(f)
        return f

    with patch("builtins.open", side_effect=tracking_open), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        
        proc = AsyncMock()
        proc.pid = 5678
        proc.returncode = None
        mock_exec.return_value = proc

        await gateway.start()

        assert len(opened_files) == 1
        # Check that parent file handle was closed
        assert opened_files[0].closed is True
