from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from mesa_qa.config import QAConfig
from mesa_qa.models import BugReport, ScenarioEvent, TesterObservation, Verdict
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
from mesa_qa.storage.paths import get_run_dir
from mesa_qa.telemetry.reports import ReportBuilder
from mesa_qa.telemetry.sampler import ResourceSampler
from mesa_qa.codex.runner import CodexRunner
from mesa_qa.codex.tester import TesterCodex
from mesa_qa.codex.repairer import RepairerCodex
from mesa_qa.judge.deterministic import DeterministicJudge
from mesa_qa.judge.classifier import AnomalyClassifier
from mesa_qa.mesa.bootstrap import MESABootstrap
from mesa_qa.mesa.approval import (
    ApprovalLifecycleError,
    MCPStatusClient,
    OfficialApprovalLifecycle,
    OperationFinalityPoller,
    OperationOwnership,
)

logger = logging.getLogger("mesa_qa.controller")

_WRITE_TOOL_BY_KIND = {
    "remember": "mesa_remember",
    "correct": "mesa_improve",
    "forget": "mesa_forget",
    "duplicate": "mesa_remember",
    "semantic_duplicate": "mesa_remember",
    "multi_fact": "mesa_remember",
    "conflict": "mesa_improve",
    "idempotency": "mesa_remember",
}


class QAController:
    def __init__(self, config: QAConfig, run_id: str):
        self.config = config
        self.run_id = run_id
        self.run_dir = get_run_dir(run_id)

        self.state_machine = StateMachine(
            initial_state=State.INIT, on_change=self._on_state_change
        )
        self.controller_db = ControllerDB(self.run_dir / "controller.db")
        self.oracle_db = OracleDB(self.run_dir / "oracle.db")
        self.oracle_eval = OracleEvaluator(self.oracle_db)

        self.process_mgr = ProcessManager(config=config, run_dir=self.run_dir)
        self.scenario_engine = ScenarioEngine(
            scenarios_dir=Path(__file__).parent.parent.parent / "scenarios",
            seed=config.run.seed,
        )

        self.codex_runner = CodexRunner(codex_binary=config.codex.binary)
        self.tester = TesterCodex(
            runner=self.codex_runner,
            prompts_dir=Path(__file__).parent.parent.parent / "prompts",
        )
        self.repairer = RepairerCodex(
            runner=self.codex_runner,
            prompts_dir=Path(__file__).parent.parent.parent / "prompts",
            python_bin=config.mesa.python_path,
        )

        self.judge = DeterministicJudge()
        self.classifier = AnomalyClassifier()
        self.policy_guard = RepairPolicyGuard(config.safety)
        self.repair_gate = RepairGate(self.policy_guard)
        self.evidence_store = EvidenceStore(self.run_dir)
        self.repair_verifier = RepairVerifier(python_bin=config.mesa.python_path)
        self.sampler = ResourceSampler(
            self.run_dir,
            warn_rss_mb=config.resources.warn_rss_mb,
            hard_stop_rss_mb=config.resources.hard_stop_rss_mb,
        )
        self.report_builder = ReportBuilder(self.run_dir)

        self._action_count = 0
        self._epoch = 0
        self._bugs: List[Dict[str, Any]] = []
        self._repairs: List[Dict[str, Any]] = []
        self._pause_requested = False
        self._stop_requested = False
        self._rng = random.Random(config.run.seed)
        self._main_baseline: Optional[Dict[str, str]] = None
        self._approval: Optional[OfficialApprovalLifecycle] = None
        self._binding_context: Optional[Dict[str, str]] = None
        self._rotation_pending_old_thread: Optional[str] = None

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        asyncio.create_task(self._persist_state())

    async def _persist_state(self) -> None:
        state_dict = {
            "run_id": self.run_id,
            "status": self.state_machine.current.value,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "baseline_main_head": str(
                self.process_mgr.worktree_mgr.check_main_hygiene().get("head")
            ),
            "candidate_branch": str(self.process_mgr.candidate_branch or ""),
            "candidate_head": str(self.process_mgr.candidate_worktree or ""),
            "candidate_worktree": str(self.process_mgr.candidate_worktree or ""),
            "qa_storage_root": str(self.run_dir / "mesa-storage"),
            "current_epoch": self._epoch,
            "action_count": self._action_count,
            "confirmed_bug_count": len(self._bugs),
            "verified_repair_count": len(
                [r for r in self._repairs if r.get("status") == "VERIFIED"]
            ),
            "scenario_cursor": self.scenario_engine.cursor,
            "scenario_seed": self.config.run.seed,
            "tester_thread_id": self.tester.thread_id,
            "mesa_pid": (
                self.process_mgr.mesa_runtime._process.pid
                if self.process_mgr.mesa_runtime
                and self.process_mgr.mesa_runtime._process
                else None
            ),
            "mcp_gateway_pid": (
                self.process_mgr.mcp_gateway._process.pid
                if self.process_mgr.mcp_gateway
                and self.process_mgr.mcp_gateway._process
                else None
            ),
        }
        await self.controller_db.save_run_state(state_dict)

    async def initialize(self) -> None:
        logger.info("Initializing MESA-QA Controller for run %s...", self.run_id)
        await self.controller_db.initialize()
        await self.oracle_db.initialize()

        # Step 1: Preflight
        self.state_machine.transition_to(State.PREFLIGHT)
        hygiene = self.process_mgr.worktree_mgr.check_main_hygiene()
        self._main_baseline = self.process_mgr.worktree_mgr.capture_main_baseline()
        logger.info(
            "Main MESA repository baseline HEAD: %s (clean: %s)",
            hygiene["head"],
            hygiene["is_clean"],
        )

        # Step 2: Create Candidate Worktree
        self.state_machine.transition_to(State.CREATE_CANDIDATE)
        candidate_wt = self.process_mgr.setup_worktree(
            self.run_id, baseline_commit=hygiene["head"]
        )
        self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
        logger.info("Candidate worktree ready at %s", candidate_wt)

        # Step 3: Start MESA Runtime
        self.state_machine.transition_to(State.START_MESA)
        await self.process_mgr.start_all()
        self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)

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
        self.tester.configure_mesa_launcher(
            binding["launcher_prefix"],
            gateway_url=self.process_mgr.mcp_gateway.gateway_url,
        )
        binding_context = dict(binding["binding_context"])
        gateway_credential = binding_context.pop("gateway_credential")
        self._binding_context = binding_context
        if self.config.approval.enabled:
            status_client = MCPStatusClient(
                gateway_url=self.process_mgr.mcp_gateway.gateway_url,
                credential=gateway_credential,
            )
            poller = OperationFinalityPoller(
                status_client,
                timeout_seconds=self.config.approval.timeout_seconds,
                interval_seconds=self.config.approval.poll_interval_seconds,
            )
            self._approval = OfficialApprovalLifecycle(
                candidate_worktree=candidate_wt,
                mesa_cli=self.config.mesa.python_path.parent / "mesa",
                mesa_admin_cli=self.config.mesa.python_path.parent / "mesa-v4-admin",
                control_db_path=self.run_dir / "gateway-control.db",
                policy_db_path=self.run_dir / "mesa-storage" / "rbac_policy.db",
                operator_principal=self.config.approval.operator_principal,
                poller=poller,
            )
            provisioned = await self._approval.provision_operator()
            scope = await self._approval.provision_qa_scope(
                mesa_api_url=self.process_mgr.mesa_runtime.base_url,
                mesa_api_key=self.process_mgr.mesa_runtime.api_key,
                api_principal=self.process_mgr.mesa_runtime.principal_id,
                actor_id=binding_context["principal_id"],
                tenant_id=binding_context["tenant_id"],
                workspace_id=binding_context["workspace_id"],
                dataset_id=binding_context["dataset_id"],
            )
            self.evidence_store.append_json_record(
                "approval_lifecycle.json",
                {
                    "run_id": self.run_id,
                    "event": "operator_principal_provisioned",
                    **provisioned,
                },
            )
            self.evidence_store.append_json_record(
                "approval_lifecycle.json",
                {
                    "run_id": self.run_id,
                    "event": "qa_runtime_scope_provisioned",
                    **scope,
                },
            )

        # Load Scenarios
        self.scenario_engine.load_suite()

        self.state_machine.transition_to(State.RUNNING)
        logger.info(
            "MESA-QA Controller initialization complete. Ready to run endurance session."
        )

    async def run_loop(self) -> None:
        logger.info(
            "Starting endurance test loop (duration: %s hours)...",
            self.config.run.duration_hours,
        )
        start_time = time.time()
        max_duration_sec = self.config.run.duration_hours * 3600

        while time.time() - start_time < max_duration_sec:
            control = await self.controller_db.get_control(self.run_id)
            if control == "pause":
                self._pause_requested = True
            elif control == "resume":
                self._pause_requested = False
            elif control == "stop":
                self._stop_requested = True
            if self.state_machine.current == State.WAITING_FOR_CODEX:
                if control == "resume":
                    self.state_machine.transition_to(State.RUNNING)
                    await self._persist_state()
                else:
                    await asyncio.sleep(1.0)
                    continue
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
            pid = (
                self.process_mgr.mesa_runtime._process.pid
                if self.process_mgr.mesa_runtime
                and self.process_mgr.mesa_runtime._process
                else None
            )
            self.sampler.sample_process(pid)

            if not self.scenario_engine.has_next():
                logger.info(
                    "End of scenario queue reached. Resetting cursor for continuous endurance..."
                )
                self.scenario_engine.reset()
                self._epoch += 1

            event = self.scenario_engine.next_event()
            if not event:
                await asyncio.sleep(5.0)
                continue

            await self._process_event(event)

            # Cadence sleep
            cadence = self._rng.uniform(
                self.config.run.cadence_seconds_min, self.config.run.cadence_seconds_max
            )
            await asyncio.sleep(cadence)

        self.state_machine.transition_to(State.STOPPING)
        await self.shutdown()
        self.state_machine.transition_to(State.COMPLETED)
        logger.info("Endurance run completed.")

    async def _process_event(self, event: ScenarioEvent) -> None:
        self._action_count += 1
        action_id = f"act_{self.run_id}_{self._action_count:06d}"
        event = event.model_copy(
            update={
                "idempotency_key": event.idempotency_key
                or f"qa:{self.run_id}:{action_id}:1"
            }
        )

        # Handle lifecycle-only events before normal Tester actions.
        if event.kind.value == "restart_runtime":
            logger.info(
                "Scenario requested runtime restart. Restarting candidate services..."
            )
            old_mesa_pid = (
                self.process_mgr.mesa_runtime._process.pid
                if self.process_mgr.mesa_runtime
                and self.process_mgr.mesa_runtime._process
                else None
            )
            old_gateway_pid = (
                self.process_mgr.mcp_gateway._process.pid
                if self.process_mgr.mcp_gateway
                and self.process_mgr.mcp_gateway._process
                else None
            )
            await self.process_mgr.restart_all()
            new_mesa_pid = (
                self.process_mgr.mesa_runtime._process.pid
                if self.process_mgr.mesa_runtime
                and self.process_mgr.mesa_runtime._process
                else None
            )
            new_gateway_pid = (
                self.process_mgr.mcp_gateway._process.pid
                if self.process_mgr.mcp_gateway
                and self.process_mgr.mcp_gateway._process
                else None
            )
            restarted = bool(
                old_mesa_pid
                and new_mesa_pid
                and old_mesa_pid != new_mesa_pid
                and old_gateway_pid
                and new_gateway_pid
                and old_gateway_pid != new_gateway_pid
            )
            restart_evidence = {
                "run_id": self.run_id,
                "scenario_event_id": event.id,
                "old_mesa_pid": old_mesa_pid,
                "new_mesa_pid": new_mesa_pid,
                "old_gateway_pid": old_gateway_pid,
                "new_gateway_pid": new_gateway_pid,
                "status": "PASS" if restarted else "FAIL",
            }
            self.evidence_store.append_json_record(
                "restart_durability.json", restart_evidence
            )
            await self.controller_db.record_action(
                action_id=action_id,
                run_id=self.run_id,
                scenario_event_id=event.id,
                action_type=event.kind.value,
                request=event.model_dump(),
                response=restart_evidence,
                verdict="PASS" if restarted else "FAIL",
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
            if not restarted:
                raise RuntimeError(
                    "candidate restart did not produce new process identities"
                )
            return
        if event.kind.value == "rotate_session":
            old_thread = self.tester.thread_id
            self.tester.rotate_thread()
            self._rotation_pending_old_thread = old_thread
            await self.controller_db.record_action(
                action_id=action_id,
                run_id=self.run_id,
                scenario_event_id=event.id,
                action_type=event.kind.value,
                request=event.model_dump(),
                response={"thread_rotated": True, "old_thread_id": old_thread},
                verdict="PASS",
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        # 2. Execute action via Tester Codex
        tester_ws = self.run_dir / "tester_workspace"
        env = os.environ.copy()
        obs = await self.tester.execute_action(event, action_id, tester_ws, mcp_env=env)
        if self._rotation_pending_old_thread is not None:
            rotation = {
                "run_id": self.run_id,
                "scenario_event_id": event.id,
                "old_thread_id": self._rotation_pending_old_thread,
                "new_thread_id": self.tester.thread_id,
                "status": (
                    "PASS"
                    if self.tester.thread_id
                    and self.tester.thread_id != self._rotation_pending_old_thread
                    else "FAIL"
                ),
            }
            self.evidence_store.append_json_record("thread_rotation.json", rotation)
            self._rotation_pending_old_thread = None
        if obs.tester_assessment == "infra_error" and "Codex" in obs.reason:
            await self.controller_db.record_action(
                action_id=action_id,
                run_id=self.run_id,
                scenario_event_id=event.id,
                action_type=event.kind.value,
                request=event.model_dump(),
                response=obs.model_dump(),
                verdict="FAIL",
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            self.evidence_store.append_json_record(
                "real_mcp_smoke.json",
                {
                    "run_id": self.run_id,
                    "scenario_event_id": event.id,
                    "status": "BLOCKED",
                    "reason": obs.reason,
                },
            )
            self.state_machine.transition_to(State.WAITING_FOR_CODEX)
            await self._persist_state()
            return

        if event.kind.value in _WRITE_TOOL_BY_KIND:
            obs = await self._finalize_write(event, obs)
            if str(obs.actual.get("operation_state", "")).upper() == "COMMITTED":
                # Ground truth advances only after real MESA durable success.
                await self.oracle_db.apply_event(event)

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

    async def _finalize_write(
        self, event: ScenarioEvent, observation: TesterObservation
    ) -> TesterObservation:
        actual = dict(observation.actual)
        operation_id = self._operation_id_from(actual)
        if not operation_id:
            actual["approval_outcome"] = "NEEDS_REVIEW"
            actual["operation_state"] = "UNKNOWN"
            return observation.model_copy(
                update={
                    "actual": actual,
                    "tester_assessment": "infra_error",
                    "reason": "write did not return exactly one operation_id",
                    "needs_recheck": True,
                }
            )
        if self._approval is None or self._binding_context is None:
            actual["approval_outcome"] = "NEEDS_REVIEW"
            actual["operation_state"] = "PENDING_APPROVAL"
            return observation.model_copy(
                update={
                    "actual": actual,
                    "tester_assessment": "infra_error",
                    "reason": "official approval lifecycle is not configured",
                    "needs_recheck": True,
                }
            )

        ownership = OperationOwnership(
            run_id=self.run_id,
            idempotency_key=str(event.idempotency_key or ""),
            tool_name=_WRITE_TOOL_BY_KIND[event.kind.value],
            client_id=self._binding_context["client_id"],
            binding_id=self._binding_context["binding_id"],
            principal_id=self._binding_context["principal_id"],
            tenant_id=self._binding_context["tenant_id"],
            workspace_id=self._binding_context["workspace_id"],
            dataset_id=self._binding_context["dataset_id"],
        )
        try:
            lifecycle = await self._approval.approve_and_wait(operation_id, ownership)
        except ApprovalLifecycleError as exc:
            failure = {
                "run_id": self.run_id,
                "scenario_event_id": event.id,
                "operation_id": operation_id,
                "outcome": exc.outcome,
                "error": str(exc),
            }
            self.evidence_store.append_json_record("approval_lifecycle.json", failure)
            actual.update(
                {
                    "operation_id": operation_id,
                    "operation_state": "UNKNOWN",
                    "approval_outcome": exc.outcome,
                    "approval_error": str(exc),
                }
            )
            return observation.model_copy(
                update={
                    "actual": actual,
                    "tester_assessment": "infra_error",
                    "reason": str(exc),
                    "needs_recheck": True,
                }
            )

        evidence = {
            "run_id": self.run_id,
            "scenario_event_id": event.id,
            "idempotency_key": event.idempotency_key,
            **lifecycle.evidence(),
        }
        self.evidence_store.append_json_record("approval_lifecycle.json", evidence)
        actual.update(
            {
                "operation_id": operation_id,
                "operation_state": lifecycle.final_status,
                "approval_outcome": lifecycle.outcome,
                "approval_invoked": lifecycle.approval_invoked,
                "status_transitions": lifecycle.status_transitions,
            }
        )
        assessment = observation.tester_assessment
        reason = observation.reason
        if lifecycle.outcome != "PASS":
            assessment = "infra_error"
            reason = f"operation reached terminal failure {lifecycle.final_status}"
        return observation.model_copy(
            update={
                "actual": actual,
                "tester_assessment": assessment,
                "reason": reason,
                "needs_recheck": lifecycle.outcome != "PASS",
            }
        )

    @staticmethod
    def _operation_id_from(actual: Dict[str, Any]) -> Optional[str]:
        direct = actual.get("operation_id")
        if isinstance(direct, str) and direct:
            return direct
        operation_ids = actual.get("operation_ids")
        if (
            isinstance(operation_ids, list)
            and len(operation_ids) == 1
            and isinstance(operation_ids[0], str)
        ):
            return operation_ids[0]
        raw = actual.get("raw_response")
        if isinstance(raw, dict):
            nested = raw.get("operation_id")
            if isinstance(nested, str) and nested:
                return nested
        return None

    async def _handle_anomaly(
        self, event: ScenarioEvent, obs: TesterObservation, verdict: Verdict
    ) -> None:
        self.state_machine.transition_to(State.ANOMALY)
        logger.warning(
            "Candidate anomaly detected for event %s: %s", event.id, verdict.reason
        )

        # Step 1: Recheck
        self.state_machine.transition_to(State.RECHECKING)
        await asyncio.sleep(3.0)  # Bounded stabilization interval

        # Step 2: Reproduce
        self.state_machine.transition_to(State.REPRODUCING)
        recheck_obs = await self.tester.execute_action(
            event, f"recheck_{obs.action_id}", self.run_dir / "tester_workspace"
        )
        recheck_verdict = await self.judge.judge(event, recheck_obs, self.oracle_eval)

        if not recheck_verdict.is_candidate_anomaly:
            logger.info(
                "Anomaly did not reproduce on recheck. Dismissing transient anomaly."
            )
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
            repeat_count=2,
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
        await self.controller_db.record_bug(
            bug_id,
            self.run_id,
            severity.value,
            category,
            bug.model_dump(),
            "CONFIRMED",
            datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "BUG CONFIRMED: %s (%s). Evidence bundle created.", bug_id, category
        )

        # Step 4: Repair if enabled
        if (
            self.config.repair.enabled
            and len(self._repairs) < self.config.repair.max_repairs_per_run
        ):
            await self._execute_repair_pipeline(bug, event)
        else:
            self.state_machine.transition_to(State.RUNNING)

    async def _execute_repair_pipeline(
        self, bug: BugReport, event: ScenarioEvent
    ) -> None:
        self.state_machine.transition_to(State.REPAIRING)
        logger.info("Starting autonomous repair pipeline for bug %s...", bug.bug_id)

        # A QA observation is not a source-path regression.  Only an explicit
        # evidence-backed command recorded by the reproduction pipeline may
        # authorize repair.  Never synthesize `expected == actual` tests.
        test_file = bug.preconditions.get("pre_fix_test_file")
        if not isinstance(test_file, str) or not test_file:
            logger.warning(
                "No genuine pre-fix source-path regression was recorded for %s; repair blocked.",
                bug.bug_id,
            )
            self._repairs.append(
                {
                    "bug_id": bug.bug_id,
                    "status": "NEEDS_REVIEW",
                    "reason": "missing genuine pre-fix regression",
                }
            )
            self.state_machine.transition_to(State.RUNNING)
            return

        # Verify PRE-FIX FAIL
        pre_fix_pass, output = self.repair_verifier.run_pytest_on_file(
            self.process_mgr.candidate_worktree, test_file
        )
        if pre_fix_pass:
            logger.warning(
                "PRE-FIX FAIL check failed (test unexpectedly passed before fix). Aborting repair."
            )
            self.state_machine.transition_to(State.RUNNING)
            return

        logger.info("PRE-FIX FAIL verified on %s.", test_file)

        # Evaluate Gates G1-G5
        gate_ok, gate_reason = self.repair_gate.evaluate_gates(
            bug=bug,
            candidate_worktree=self.process_mgr.candidate_worktree,
            stable_reproduction_proven=bug.repeat_count >= 2,
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
        post_fix_pass, _ = self.repair_verifier.run_pytest_on_file(
            self.process_mgr.candidate_worktree, test_file
        )

        if post_fix_pass:
            sha = self.repair_verifier.commit_repair(
                self.process_mgr.candidate_worktree,
                bug.bug_id,
                bug.category,
                self.policy_guard.changed_paths(self.process_mgr.candidate_worktree),
            )
            repair_res.success = True
            repair_res.post_fix_test_passed = True
            repair_res.commit_sha = sha

            self._repairs.append(
                {"bug_id": bug.bug_id, "status": "VERIFIED", "commit_sha": sha}
            )

            # Step 5: Restart candidate and live repro
            self.state_machine.transition_to(State.RESTARTING)
            await self.process_mgr.restart_all()

            self.state_machine.transition_to(State.LIVE_RECHECK)
            live_obs = await self.tester.execute_action(
                event, f"live_{bug.bug_id}", self.run_dir / "tester_workspace"
            )
            live_verdict = await self.judge.judge(event, live_obs, self.oracle_eval)

            if live_verdict.is_pass:
                logger.info(
                    "LIVE REPRO PASSED! Bug %s resolved and verified on candidate runtime.",
                    bug.bug_id,
                )
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
        if self._main_baseline is not None:
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)

        # Save final reports
        state_dict = await self.controller_db.get_run_state(self.run_id) or {
            "run_id": self.run_id,
            "status": self.state_machine.current.value,
        }
        self.report_builder.generate_final_report(state_dict, self._bugs, self._repairs)
        logger.info("Shutdown complete.")
