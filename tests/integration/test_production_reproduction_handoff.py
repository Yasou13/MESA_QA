from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent, TesterObservation, Verdict
from mesa_qa.repair.verification import RepairVerifier
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.state_machine import State


def _candidate(tmp_path: Path) -> tuple[Path, WorktreeManager, str]:
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.email", "qa@example.test"], cwd=main, check=True)
    (main / "README.md").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=main, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=main, check=True, capture_output=True)
    manager = WorktreeManager(main, tmp_path / "candidates")
    candidate, _branch, sha = manager.create_candidate_worktree("reproduction")
    return candidate, manager, sha


@pytest.mark.asyncio
async def test_anomaly_path_materializes_and_executes_live_regression_handoff(tmp_path, monkeypatch):
    """The normal anomaly path creates the regression; no test injects it."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    candidate, manager, base_sha = _candidate(tmp_path)
    workspace = tmp_path / "tester"
    workspace.mkdir()
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"action_id\":\"reproduce_BUG-0001\",\"scenario_event_id\":\"evt-memory\",\"tools_called\":[\"mesa_remember\"],\"actual\":{\"operation_state\":\"FAILED\"}}'\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    config = QAConfig.load()
    config.codex.binary = str(fake_codex)
    controller = QAController(config, run_id="production-reproduction")
    await controller.controller_db.initialize()
    controller.process_mgr.worktree_mgr = manager
    controller.process_mgr.candidate_worktree = candidate
    controller.process_mgr.candidate_branch = "qa/autonomous-reproduction"
    controller.process_mgr.candidate_base_sha = base_sha
    controller.tester.configure_mesa_launcher(["env"])
    (controller.run_dir / "tester_workspace").mkdir(parents=True)

    event = ScenarioEvent(
        id="evt-memory", kind=ActionKind.REMEMBER, entity="atlas", field="backend",
        value="FastAPI", text="Atlas uses FastAPI.",
        idempotency_key="qa:production-reproduction:initial:1",
    )
    observation = TesterObservation(
        action_id="initial", scenario_event_id=event.id,
        actual={"operation_state": "FAILED"},
    )
    anomaly = Verdict(is_pass=False, is_candidate_anomaly=True, category="WRITE", expected="COMMITTED", actual="FAILED")
    controller.tester.execute_action = AsyncMock(return_value=observation)
    controller.judge.judge = AsyncMock(return_value=anomaly)
    controller.state_machine._current_state = State.RUNNING

    await controller._handle_anomaly(event, observation, anomaly)

    bug = controller._bugs[0]
    regression = bug["preconditions"]["pre_fix_test_file"]
    assert regression.startswith("tests/mesa_qa_regressions/")
    assert Path(bug["preconditions"]["reproduction_spec"]).is_file()
    assert bug["candidate_commit_before"] == base_sha
    verifier = RepairVerifier(Path(sys.executable))
    pre_fix_ok, output = verifier.verify_pre_fix_failure(candidate, regression)
    assert pre_fix_ok, output
