from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from mesa_qa.codex.runner import CodexRunner
from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.repair.verification import RepairVerifier


def test_config_clean_schema():
    cfg = QAConfig.load()
    assert not hasattr(cfg.candidate, "reuse_existing")
    assert not hasattr(cfg.run, "epoch_actions")
    assert not hasattr(cfg.run, "restart_every_minutes")
    assert not hasattr(cfg.run, "parallel_actions")


@pytest.mark.asyncio
async def test_codex_runner_wires_model_and_json():
    runner = CodexRunner(codex_binary="codex-test")
    
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc
        with patch.object(runner, "_capture_output", new_callable=AsyncMock) as mock_cap:
            mock_cap.return_value = (b"", b"")
            await runner.run(
                prompt="test",
                cwd=Path("/tmp"),
                model="gpt-5-preview",
                json_events=True,
            )

        args, kwargs = mock_exec.call_args
        assert "codex-test" in args
        assert "exec" in args
        assert "--json" in args
        assert "-m" in args
        assert "gpt-5-preview" in args


def test_controller_initializes_codex_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    cfg.codex.tester_model = "custom-tester-model"
    cfg.codex.repair_model = "custom-repair-model"
    cfg.codex.tester_timeout_seconds = 123
    cfg.codex.repair_timeout_seconds = 456
    cfg.codex.json_events = True

    controller = QAController(cfg, run_id="run-config-wire")
    assert controller.tester.model == "custom-tester-model"
    assert controller.tester.timeout_seconds == 123
    assert controller.tester.json_events is True
    assert controller.repairer.model == "custom-repair-model"
    assert controller.repairer.timeout_seconds == 456
    assert controller.repairer.json_events is True


def test_repair_verifier_run_full_suite(tmp_path):
    verifier = RepairVerifier(python_bin=Path("/bin/true"))
    passed, output = verifier.run_full_suite(tmp_path)
    assert passed
