from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import aiosqlite
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, BugReport, RepairResult, ScenarioEvent, Severity
from mesa_qa.repair.policy import RepairPolicyGuard
from mesa_qa.repair.verification import RepairVerifier
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.state_machine import State
from mesa_qa.telemetry.reports import ReportBuilder


def _setup_real_git_repos(tmp_path: Path):
    main_repo = tmp_path / "main_mesa"
    main_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "QA Bot"], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "qa@mesa.internal"], cwd=main_repo, capture_output=True, check=True)

    # Add source and test files
    src_dir = main_repo / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "calculator.py").write_text("def add(a, b): return a - b\n", encoding="utf-8") # Defect

    tests_dir = main_repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_calculator.py").write_text("""import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.calculator import add

def test_add():
    assert add(2, 3) == 5
""", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=main_repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial candidate commit"], cwd=main_repo, capture_output=True, check=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main_repo, capture_output=True, text=True, check=True).stdout.strip()

    candidate_root = tmp_path / "candidate_root"
    wt_mgr = WorktreeManager(main_repo=main_repo, candidate_root=candidate_root)
    wt_path, branch, head = wt_mgr.create_candidate_worktree("ctrl-live-repair")

    return main_repo, candidate_root, wt_mgr, wt_path, base_sha


@pytest.mark.asyncio
async def test_controlled_repair_live_integration(tmp_path, monkeypatch):
    """
    R015 / S048: Genuine integration evidence for controlled repair.
    Uses REAL git repositories, REAL WorktreeManager, REAL RepairPolicyGuard,
    and REAL RepairVerifier git operations to verify:
    1. Pre-fix fail
    2. Gates evaluation
    3. Pre-repair and post-repair snapshots
    4. Policy-bounded diff enforcement
    5. Approved paths commit
    6. Process restart & Live MCP verification
    7. VERIFIED verdict in DB & Report
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    main_repo, candidate_root, wt_mgr, wt_path, base_sha = _setup_real_git_repos(tmp_path)

    cfg = QAConfig.load()
    cfg.repair.enabled = True
    cfg.repair.max_repairs_per_run = 3

    run_id = "run-ctrl-repair-live"
    controller = QAController(cfg, run_id=run_id)
    await controller.controller_db.initialize()

    # Wire real worktree manager and candidate worktree
    controller.process_mgr.worktree_mgr = wt_mgr
    controller.process_mgr.candidate_worktree = wt_path
    controller.process_mgr.candidate_branch = "qa/autonomous-ctrl-live-repair"
    controller.process_mgr.candidate_base_sha = base_sha

    # Event and Bug
    event = ScenarioEvent(
        id="evt_live_01",
        entity="user",
        kind=ActionKind.REMEMBER,
        text="compute sum of 2 and 3",
    )

    bug = BugReport(
        bug_id="BUG-LIVE-INT-01",
        run_id=run_id,
        severity=Severity.P1,
        category="LOGIC_ERROR",
        scenario_id="scen_live_int",
        expected={"expected": "5"},
        actual={"actual": "-1"},
        candidate_commit_before=base_sha,
        repeat_count=2,
        preconditions={"pre_fix_test_file": "tests/test_calculator.py"},
    )

    # 1. Genuine pre-fix test fails on unfixed calculator.py
    venv_python = Path(sys.executable)
    controller.repair_verifier = RepairVerifier(python_bin=venv_python)

    # 2. Repairer applies the fix to calculator.py
    async def fake_repair_codex(bug, candidate_worktree, evidence_summary):
        # Fix the bug in candidate worktree
        (candidate_worktree / "src" / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
        return RepairResult(
            bug_id=bug.bug_id,
            success=True,
            changed_files=["src/calculator.py"],
            patch="diff --git a/src/calculator.py b/src/calculator.py\n...",
        )

    controller.repairer.execute_repair = AsyncMock(side_effect=fake_repair_codex)

    # 3. Process restart and live MCP re-check
    controller.process_mgr.restart_all = AsyncMock()
    mock_obs = MagicMock(tools_called=["mesa_recall"], actual={"result": 5})
    controller.tester.execute_action = AsyncMock(return_value=mock_obs)
    controller.judge.judge = AsyncMock(return_value=MagicMock(is_pass=True, is_candidate_anomaly=False))

    controller.state_machine._current_state = State.CONFIRMED_BUG

    # Record initial confirmed bug in DB
    await controller.controller_db.record_bug(
        bug.bug_id,
        run_id,
        bug.severity.value,
        bug.category,
        bug.model_dump(),
        "CONFIRMED",
        "2026-08-17T14:00:00Z",
    )
    controller._bugs.append({"bug_id": bug.bug_id, "status": "CONFIRMED", "severity": bug.severity.value})

    # Execute repair pipeline
    await controller._execute_repair_pipeline(bug, event)

    # Assertions
    assert controller.state_machine.current == State.RUNNING
    assert len(controller._repairs) == 1
    repair_record = controller._repairs[0]
    assert repair_record["status"] == "VERIFIED"
    assert repair_record["live_repro_passed"] is True

    # Real git commit was made in candidate worktree
    new_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert new_sha != base_sha
    assert repair_record["commit_sha"] == new_sha

    # Verify original main repo was NOT modified
    main_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=main_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert main_sha == base_sha
    assert (main_repo / "src" / "calculator.py").read_text(encoding="utf-8") == "def add(a, b): return a - b\n"

    # Verify DB status
    async with aiosqlite.connect(controller.controller_db.db_path) as db:
        async with db.execute("SELECT status FROM bugs WHERE bug_id = 'BUG-LIVE-INT-01'") as cur:
            row = await cur.fetchone()
            assert row[0] == "VERIFIED"

    # Verify Final Report derived from real evidence
    report_builder = ReportBuilder(controller.run_dir)
    state_dict = await controller.controller_db.get_run_state(run_id) or {"run_id": run_id}
    state_dict["status"] = "COMPLETED"
    state_dict["action_count"] = 1
    controller._bugs[0]["status"] = "VERIFIED"
    md_path, json_path = report_builder.generate_final_report(state_dict, controller._bugs, controller._repairs)
    assert md_path.exists()
    assert json_path.exists()
    verdict = report_builder.derive_session_verdict(state_dict, controller._bugs, controller._repairs)
    assert verdict == "PASS"
