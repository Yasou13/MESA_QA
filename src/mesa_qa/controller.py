from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import logging

from mesa_qa.config import QAConfig
from mesa_qa.models import BugReport, RepairResult, ScenarioEvent, Severity, TesterObservation, Verdict
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator
from mesa_qa.repair.evidence import EvidenceStore
from mesa_qa.repair.gate import RepairGate
from mesa_qa.repair.policy import RepairPolicyGuard
from mesa_qa.repair.verification import RepairVerifier
from mesa_qa.runtime.process_manager import ProcessManager
from mesa_qa.scenario.engine import ScenarioEngine
from mesa_qa.state_machine import State, StateMachine
from mesa_qa.storage.controller_db import ControllerDB
from mesa_qa.storage.paths import get_run_dir, assert_safe_paths
from mesa_qa.telemetry.reports import ReportBuilder
from mesa_qa.telemetry.sampler import ResourceSampler
from mesa_qa.codex.runner import CodexRunner
from mesa_qa.codex.tester import TesterCodex
from mesa_qa.codex.repairer import RepairerCodex
from mesa_qa.judge.deterministic import DeterministicJudge
from mesa_qa.judge.classifier import AnomalyClassifier
from mesa_qa.mesa.bootstrap import MESABootstrap

logger = logging.getLogger("mesa_qa.controller")


class QAController:
    def __init__(self, config: QAConfig, run_id: str):
        self.config = config
        self.run_id = run_id
        self.run_dir = get_run_dir(run_id)

        self.state_machine = StateMachine(initial_state=State.INIT, on_change=self._on_state_change)
        self.controller_db = ControllerDB(self.run_dir / "controller.db")
        self.oracle_db = OracleDB(self.run_dir / "oracle.db")
        self.oracle_eval = OracleEvaluator(self.oracle_db)

        self.process_mgr = ProcessManager(config=config, run_dir=self.run_dir)
        self.scenario_engine = ScenarioEngine(scenarios_dir=Path(__file__).parent.parent.parent / "scenarios", seed=config.run.seed)

        self.codex_runner = CodexRunner(codex_binary=config.codex.binary)
        self.tester = TesterCodex(runner=self.codex_runner, prompts_dir=Path(__file__).parent.parent.parent / "prompts")
        self.repairer = RepairerCodex(runner=self.codex_runner, prompts_dir=Path(__file__).parent.parent.parent / "prompts", python_bin=config.mesa.python_path)

        self.judge = DeterministicJudge()
        self.classifier = AnomalyClassifier()
        self.policy_guard = RepairPolicyGuard(config.safety)
        self.repair_gate = RepairGate(self.policy_guard)
        self.evidence_store = EvidenceStore(self.run_dir)
        self.repair_verifier = RepairVerifier(python_bin=config.mesa.python_path)
        self.sampler = ResourceSampler(self.run_dir, warn_rss_mb=config.resources.warn_rss_mb, hard_stop_rss_mb=config.resources.hard_stop_rss_mb)
        self.report_builder = ReportBuilder(self.run_dir)

        self._action_count = 0
        self._epoch = 0
        self._bugs: List[Dict[str, Any]] = []
        self._repairs: List[Dict[str, Any]] = []
        self._pause_requested = False
        self._stop_requested = False

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        asyncio.create_task(self._persist_state())

    async def _persist_state(self) -> None:
        state_dict = {
            "run_id": self.run_id,
            "status": self.state_machine.current.value,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "baseline_main_head": str(self.process_mgr.worktree_mgr.check_main_hygiene().get("head")),
            "candidate_branch": str(self.process_mgr.candidate_branch or ""),
            "candidate_head": str(self.process_mgr.candidate_worktree or ""),
            "candidate_worktree": str(self.process_mgr.candidate_worktree or ""),
            "qa_storage_root": str(self.run_dir / "mesa-storage"),
            "current_epoch": self._epoch,
            "action_count": self._action_count,
            "confirmed_bug_count": len(self._bugs),
            "verified_repair_count": len([r for r in self._repairs if r.get("status") == "VERIFIED"]),
        }
        await self.controller_db.save_run_state(state_dict)

    async def initialize(self) -> None:
        logger.info("Initializing MESA-QA Controller for run %s...", self.run_id)
        await self.controller_db.initialize()
        await self.oracle_db.initialize()

        # Step 1: Preflight
        self.state_machine.transition_to(State.PREFLIGHT)
        hygiene = self.process_mgr.worktree_mgr.check_main_hygiene()
        logger.info("Main MESA repository baseline HEAD: %s (clean: %s)", hygiene["head"], hygiene["is_clean"])

        # Step 2: Create Candidate Worktree
        self.state_machine.transition_to(State.CREATE_CANDIDATE)
        candidate_wt = self.process_mgr.setup_worktree(self.run_id, baseline_commit=hygiene["head"])
        logger.info("Candidate worktree ready at %s", candidate_wt)

        # Step 3: Start MESA Runtime
        self.state_machine.transition_to(State.START_MESA)
        await self.process_mgr.start_all()

        # Step 4: Start MCP & Provision Binding
        self.state_machine.transition_to(State.START_MCP)
        bootstrap = MESABootstrap(
            candidate_worktree=candidate_wt,
            python_bin=self.config.mesa.python_path,
            control_db_path=self.run_dir / "gateway-control.db",
            gateway_url=self.process_mgr.mcp_gateway.gateway_url,
        )
        tester_ws = self.run_dir / "tester_workspace"
        binding = bootstrap.prepare_tester_workspace(tester_ws)
        self.tester.configure_mesa_launcher(binding["launcher_prefix"])

        # Load Scenarios
        self.scenario_engine.load_suite()

        self.state_machine.transition_to(State.RUNNING)
        logger.info("MESA-QA Controller initialization complete. Ready to run endurance session.")

    async def run_loop(self) -> None:
        logger.info("Starting endurance test loop (duration: %s hours)...", self.config.run.duration_hours)
        start_time = time.time()
        max_duration_sec = self.config.run.duration_hours * 3600

        while time.time() - start_time < max_duration_sec:
            if self._stop_requested:
                logger.info("Stop requested. Exiting endurance loop.")
                break

            if self._pause_requested:
                self.state_machine.transition_to(State.PAUSED)
                logger.info("Controller PAUSED. Waiting for resume...")
                while self._pause_requested and not self._stop_requested:
                    await asyncio.sleep(2.0)
                if not self._stop_requested:
                    self.state_machine.transition_to(State.RUNNING)

            # Check resources
            pid = self.process_mgr.mesa_runtime._process.pid if self.process_mgr.mesa_runtime and self.process_mgr.mesa_runtime._process else None
            self.sampler.sample_process(pid)

            if not self.scenario_engine.has_next():
                logger.info("End of scenario queue reached. Resetting cursor for continuous endurance...")
                self.scenario_engine.reset()
                self._epoch += 1

        event = self.scenario_engine.next_event()
            if not event:
                await asyncio.sleep(5.0)
                continue

            await self._process_event(event)

            # Cadence sleep
            cadence = self.config.run.cadence_seconds_min
            await asyncio.sleep(cadence)

        self.state_machine.transition_to(State.STOPPING)
        await self.shutdown()
        self.state_machine.transition_to(State.COMPLETED)
        logger.info("Endurance run completed.")

    async def _process_event(self, event: ScenarioEvent) -> None:
        self._action_count += 1
        action_id = f"act_{self.run_id}_{self._action_count:06d}"
        event = event.model_copy(update={"idempotency_key": event.idempotency_key or f"qa:{self.run_id}:{action_id}:1"})

        # 1. Apply event to Ground Truth Oracle
        await self.oracle_db.apply_event(event)

        # Handle process restart event in scenario
        if event.kind.value == "restart_runtime":
            logger.info("Scenario requested runtime restart. Restarting candidate services...")
            await self.process_mgr.restart_all()
            return
        if event.kind.value == "rotate_session":
            self.tester.rotate_thread()
            await self.controller_db.record_action(
                action_id=action_id, run_id=self.run_id, scenario_event_id=event.id,
                action_type=event.kind.value, request=event.model_dump(),
                response={"thread_rotated": True}, verdict="PASS",
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        # 2. Execute action via Tester Codex
        tester_ws = self.run_dir / "tester_workspace"
        env = os.environ.copy()
        obs = await self.tester.execute_action(event, action_id, tester_ws, mcp_env=env)

        # 3. Judge output against Oracle
        verdict = await self.judge.judge(event, obs, self.oracle_eval)

        # 4. Record action log
        await self.controller_db.record_action(
            action_id=action_id,
            run_id=self.run_id,
            scenario_event_id=event.id,
            action_type=event.kind.value,
            request=event.model_dump(),
            response=obs.model_dump(),
            verdict="PASS" if verdict.is_pass else "FAIL",
            executed_at=datetime.now(timezone.utc).isoformat(),
        )

        if verdict.is_candidate_anomaly:
            await self._handle_anomaly(event, obs, verdict)

    async def _handle_anomaly(self, event: ScenarioEvent, obs: TesterObservation, verdict: Verdict) -> None:
        self.state_machine.transition_to(State.ANOMALY)
        logger.warning("Candidate anomaly detected for event %s: %s", event.id, verdict.reason)

        # Step 1: Recheck
        self.state_machine.transition_to(State.RECHECKING)
        await asyncio.sleep(3.0)  # Bounded stabilization interval

        # Step 2: Reproduce
        self.state_machine.transition_to(State.REPRODUCING)
        recheck_obs = await self.tester.execute_action(event, f"recheck_{obs.action_id}", self.run_dir / "tester_workspace")
        recheck_verdict = await self.judge.judge(event, recheck_obs, self.oracle_eval)

        if not recheck_verdict.is_candidate_anomaly:
            logger.info("Anomaly did not reproduce on recheck. Dismissing transient anomaly.")
            self.state_machine.transition_to(State.RUNNING)
            return

        # Step 3: Confirmed Bug
        self.state_machine.transition_to(State.CONFIRMED_BUG)
        severity, category = self.classifier.classify(verdict, event.kind.value)
        bug_id = f"BUG-{len(self._bugs)+1:04d}"

        bug = BugReport(
            bug_id=bug_id,
            run_id=self.run_id,
            severity=severity,
            category=category,
            scenario_id=event.id,
            steps=[event.model_dump()],
            expected={"expected": verdict.expected},
            actual={"actual": verdict.actual},
            candidate_commit_before=str(self.process_mgr.candidate_branch),
        )

        # Create Evidence Bundle
        self.evidence_store.create_bundle(
            bug=bug,
            user_sequence=[event.model_dump()],
            expected_data={"expected": verdict.expected},
            actual_data={"actual": verdict.actual},
        )
        self._bugs.append(bug.model_dump())
        await self.controller_db.record_bug(bug_id, self.run_id, severity.value, category, bug.model_dump(), "CONFIRMED", datetime.now(timezone.utc).isoformat())

        logger.info("BUG CONFIRMED: %s (%s). Evidence bundle created.", bug_id, category)

        # Step 4: Repair if enabled
        if self.config.repair.enabled and len(self._repairs) < self.config.repair.max_repairs_per_run:
            await self._execute_repair_pipeline(bug, event)
        else:
            self.state_machine.transition_to(State.RUNNING)

    async def _execute_repair_pipeline(self, bug: BugReport, event: ScenarioEvent) -> None:
        self.state_machine.transition_to(State.REPAIRING)
        logger.info("Starting autonomous repair pipeline for bug %s...", bug.bug_id)

        # Create Pre-fix regression test file
        test_file = self.repair_verifier.create_regression_test(self.process_mgr.candidate_worktree, bug)

        # Verify PRE-FIX FAIL
        pre_fix_pass, output = self.repair_verifier.run_pytest_on_file(self.process_mgr.candidate_worktree, test_file)
        if pre_fix_pass:
            logger.warning("PRE-FIX FAIL check failed (test unexpectedly passed before fix). Aborting repair.")
            self.state_machine.transition_to(State.RUNNING)
            return

        logger.info("PRE-FIX FAIL verified on %s.", test_file)

        # Evaluate Gates G1-G5
        gate_ok, gate_reason = self.repair_gate.evaluate_gates(
            bug=bug,
            candidate_worktree=self.process_mgr.candidate_worktree,
            stable_reproduction_proven=True,
            pre_fix_test_exists=True,
        )

        if not gate_ok:
            logger.warning("Repair gate rejected repair: %s", gate_reason)
            self.state_machine.transition_to(State.RUNNING)
            return

        # Invoke Repairer Codex
        self.state_machine.transition_to(State.VERIFYING)
        repair_res = await self.repairer.execute_repair(
            bug=bug,
            candidate_worktree=self.process_mgr.candidate_worktree,
            evidence_summary=f"Bug ID: {bug.bug_id}\nExpected: {bug.expected}\nActual: {bug.actual}",
        )

        # Verify POST-FIX PASS
        post_fix_pass, _ = self.repair_verifier.run_pytest_on_file(self.process_mgr.candidate_worktree, test_file)

        if post_fix_pass:
            sha = self.repair_verifier.commit_repair(self.process_mgr.candidate_worktree, bug.bug_id, bug.category)
            repair_res.success = True
            repair_res.post_fix_test_passed = True
            repair_res.commit_sha = sha

            self._repairs.append({"bug_id": bug.bug_id, "status": "VERIFIED", "commit_sha": sha})

            # Step 5: Restart candidate and live repro
            self.state_machine.transition_to(State.RESTARTING)
            await self.process_mgr.restart_all()

            self.state_machine.transition_to(State.LIVE_RECHECK)
            live_obs = await self.tester.execute_action(event, f"live_{bug.bug_id}", self.run_dir / "tester_workspace")
            live_verdict = await self.judge.judge(event, live_obs, self.oracle_eval)

            if live_verdict.is_pass:
                logger.info("LIVE REPRO PASSED! Bug %s resolved and verified on candidate runtime.", bug.bug_id)
            else:
                logger.warning("Live repro failed post-restart for bug %s.", bug.bug_id)

        else:
            logger.warning("Post-fix test failed for bug %s.", bug.bug_id)

        self.state_machine.transition_to(State.RUNNING)

    def pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False

    async def stop(self) -> None:
        self._stop_requested = True
        if self.state_machine.current != State.STOPPING:
            self.state_machine.transition_to(State.STOPPING)
        await self.shutdown()

    async def shutdown(self) -> None:
        logger.info("Shutting down MESA-QA processes gracefully...")
        await self.process_mgr.stop_all()

        # Save final reports
        state_dict = await self.controller_db.get_run_state(self.run_id) or {"run_id": self.run_id, "status": self.state_machine.current.value}
        self.report_builder.generate_final_report(state_dict, self._bugs, self._repairs)
        logger.info("Shutdown complete.")
