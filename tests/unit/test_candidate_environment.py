from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mesa_qa.config import QAConfig
from mesa_qa.runtime.candidate_environment import (
    CandidateEnvironmentError,
    CandidateEnvironmentManager,
    CandidatePythonEnvironment,
)
from mesa_qa.runtime.mesa_runtime import MesaCandidateRuntime
from mesa_qa.runtime.process_manager import ProcessManager


def _fake_python(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"VERSION = {version!r}\n"
        "if '-c' in sys.argv:\n"
        "    print(VERSION)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _candidate_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "candidate"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'mesa'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return repo


def test_candidate_python_313_is_rejected_before_bootstrap(tmp_path):
    candidate = _candidate_repo(tmp_path)
    py313 = _fake_python(tmp_path / "py313" / "python", "3.13.12")
    cfg = QAConfig.load()
    cfg.candidate.python_path = py313
    manager = CandidateEnvironmentManager(cfg.candidate, tmp_path / "qa-run")

    with (
        patch(
            "mesa_qa.runtime.candidate_environment.shutil.which",
            return_value="/fake/uv",
        ),
        patch.object(manager, "_run_uv") as bootstrap,
    ):
        with pytest.raises(
            CandidateEnvironmentError,
            match="Unsupported candidate Python runtime 3.13.12",
        ):
            manager.prepare(candidate)

    bootstrap.assert_not_called()


def test_candidate_python_312_bootstraps_in_qa_owned_space_without_touching_main(
    tmp_path,
):
    candidate = _candidate_repo(tmp_path)
    base_python = _fake_python(tmp_path / "python312" / "python", "3.12.9")
    original_env = tmp_path / "original-mesa" / ".venv"
    original_env.mkdir(parents=True)
    sentinel = original_env / "sentinel"
    sentinel.write_text("leave-me-alone", encoding="utf-8")

    cfg = QAConfig.load()
    cfg.candidate.python_path = base_python
    manager = CandidateEnvironmentManager(cfg.candidate, tmp_path / "qa-run")
    candidate_python = manager.environment_root / "bin" / "python"
    _fake_python(candidate_python, "3.12.9")

    with (
        patch(
            "mesa_qa.runtime.candidate_environment.shutil.which",
            return_value="/fake/uv",
        ),
        patch.object(manager, "_run_uv") as bootstrap,
    ):
        environment = manager.prepare(candidate)

    assert environment.python_bin == candidate_python.absolute()
    assert environment.version == "3.12.9"
    assert environment.environment_root.is_relative_to((tmp_path / "qa-run").resolve())
    assert sentinel.read_text(encoding="utf-8") == "leave-me-alone"
    assert bootstrap.call_args.args[0][1:] == [
        "sync",
        "--locked",
        "--active",
        "--extra",
        "dev",
    ]


@pytest.mark.asyncio
async def test_process_manager_uses_one_candidate_python_for_runtime_and_gateway(
    tmp_path,
):
    cfg = QAConfig.load()
    run_dir = tmp_path / "qa-run"
    run_dir.mkdir()
    pm = ProcessManager(cfg, run_dir)
    pm.candidate_worktree = tmp_path / "candidate"
    pm.candidate_worktree.mkdir()
    candidate_python = _fake_python(
        run_dir / "candidate-runtime" / "venv" / "bin" / "python", "3.12.9"
    )
    environment = CandidatePythonEnvironment(
        base_python=candidate_python,
        python_bin=candidate_python,
        environment_root=candidate_python.parent.parent,
        version="3.12.9",
    )

    with (
        patch(
            "mesa_qa.runtime.process_manager.CandidateEnvironmentManager.prepare",
            return_value=environment,
        ),
        patch("mesa_qa.runtime.process_manager.MesaCandidateRuntime") as runtime_cls,
        patch("mesa_qa.runtime.process_manager.MesaMCPGatewayProcess") as gateway_cls,
    ):
        runtime = AsyncMock()
        runtime.wait_until_ready.return_value = True
        runtime.base_url = "http://127.0.0.1:18000"
        runtime_cls.return_value = runtime
        gateway = AsyncMock()
        gateway.wait_until_ready.return_value = True
        gateway_cls.return_value = gateway

        await pm.start_all()

    assert runtime_cls.call_args.kwargs["python_bin"] == candidate_python
    assert gateway_cls.call_args.kwargs["python_bin"] == candidate_python
    assert pm.candidate_python == candidate_python


@pytest.mark.asyncio
async def test_migration_bearing_runtime_uses_resolved_candidate_python(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    candidate_python = _fake_python(
        tmp_path / "candidate-runtime" / "venv" / "bin" / "python", "3.12.9"
    )
    runtime = MesaCandidateRuntime(
        candidate_worktree=candidate,
        python_bin=candidate_python,
        storage_root=tmp_path / "storage",
    )
    fake_process = AsyncMock()
    fake_process.pid = 123
    fake_process.returncode = None

    with patch(
        "mesa_qa.runtime.mesa_runtime.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_process),
    ) as launch:
        await runtime.start()

    assert launch.call_args.args[:3] == (
        str(candidate_python.resolve()),
        "-m",
        "mesa_memory.runtime_entrypoint",
    )
