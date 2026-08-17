from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from mesa_qa.config import QAConfig
from mesa_qa.models import ActionKind, BugReport, ScenarioEvent, TesterObservation, Verdict
from mesa_qa.oracle.db import OracleDB
from mesa_qa.oracle.evaluator import OracleEvaluator
from mesa_qa.repair.evidence import EvidenceStore
from mesa_qa.repair.gate import RepairGate
from mesa_qa.repair.policy import RepairPolicyGuard
from mesa_qa.repair.verification import RepairVerifier
from mesa_qa.repair.reproduction import ProductionReproducer
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
            gateway_url=f"http://127.0.0.1:{config.mesa.gateway_port}",
            timeout_seconds=config.codex.tester_timeout_seconds,
            model=config.codex.tester_model,
            json_events=config.codex.json_events,
        )
        self.repairer = RepairerCodex(
            runner=self.codex_runner,
            prompts_dir=Path(__file__).parent.parent.parent / "prompts",
            python_bin=config.mesa.python_path,
            timeout_seconds=config.codex.repair_timeout_seconds,
            model=config.codex.repair_model,
            json_events=config.codex.json_events,
        )

        self.judge = DeterministicJudge()
        self.classifier = AnomalyClassifier()
        self.policy_guard = RepairPolicyGuard(config.safety)
        self.repair_gate = RepairGate(self.policy_guard)
        self.evidence_store = EvidenceStore(self.run_dir)
        self.repair_verifier = RepairVerifier(python_bin=config.mesa.python_path)
        self.reproducer = ProductionReproducer()
        self.sampler = ResourceSampler(
            self.run_dir,
            warn_rss_mb=config.resources.warn_rss_mb,
            hard_stop_rss_mb=config.resources.hard_stop_rss_mb,
        )
        self.report_builder = ReportBuilder(self.run_dir)

        self._started_at: str = datetime.now(timezone.utc).isoformat()
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
        self._rotation_pending_action_id: Optional[str] = None
        self._current_action_task: Optional[asyncio.Task[Any]] = None
        self._resource_monitor_task: Optional[asyncio.Task[Any]] = None

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        logger.debug("State transition notified: %s -> %s", old_state, new_state)

    async def _persist_state(self) -> None:
        state_dict = {
            "run_id": self.run_id,
            "status": self.state_machine.current.value,
            "started_at": self._started_at,
            "baseline_main_head": str(
                self.process_mgr.worktree_mgr.check_main_hygiene().get("head")
            ),
            "baseline_main_json": json.dumps(self._main_baseline) if self._main_baseline is not None else None,
            "candidate_base_sha": str(self.process_mgr.candidate_base_sha or ""),
            "candidate_branch": str(self.process_mgr.candidate_branch or ""),
            "candidate_head": str(
                self.process_mgr.worktree_mgr._run_git(
                    self.process_mgr.candidate_worktree, ["rev-parse", "HEAD"]
                ).strip()
                if self.process_mgr.candidate_worktree
                and self.process_mgr.candidate_worktree.exists()
                else ""
            ),
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

    async def _set_state(self, new_state: State) -> None:
        """Atomically transition state machine and await run state persistence."""
        self.state_machine.transition_to(new_state)
        await self._persist_state()

    async def initialize(self) -> None:
        logger.info("Initializing MESA-QA Controller for run %s...", self.run_id)
        await self.controller_db.initialize()
        await self.oracle_db.initialize()

        existing = await self.controller_db.get_run_state(self.run_id)
        if existing is not None:
            raise FileExistsError(f"Run ID collision: run '{self.run_id}' already has existing state in database.")

        try:
            # Step 1: Preflight
            await self._set_state(State.PREFLIGHT)
            hygiene = self.process_mgr.worktree_mgr.check_main_hygiene()
            self._main_baseline = self.process_mgr.worktree_mgr.capture_main_baseline()
            self.evidence_store.save_json("main_baseline.json", self._main_baseline)
            logger.info(
                "Main MESA repository baseline HEAD: %s (clean: %s)",
                hygiene["head"],
                hygiene["is_clean"],
            )

            # Step 2: Create Candidate Worktree
            await self._set_state(State.CREATE_CANDIDATE)
            candidate_wt = self.process_mgr.setup_worktree(
                self.run_id,
                candidate_ref=self.config.mesa.candidate_ref,
                baseline_commit=hygiene["head"] if not self.config.mesa.candidate_ref else None,
            )
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
            logger.info(
                "Candidate worktree ready at %s (base SHA %s)",
                candidate_wt,
                self.process_mgr.candidate_base_sha,
            )

            # Step 3: Start MESA Runtime
            await self._set_state(State.START_MESA)
            await self.process_mgr.start_all()
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)

            # Step 4: Start MCP & Provision Binding
            await self._set_state(State.START_MCP)
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

            await self._set_state(State.RUNNING)
            logger.info(
                "MESA-QA Controller initialization complete. Ready to run endurance session."
            )
        except Exception as exc:
            logger.exception("Fatal error during initialization: %s", exc)
            if self.state_machine.current != State.FAILED:
                await self._set_state(State.FAILED)
            await self.process_mgr.stop_all()
            if self._main_baseline is not None:
                self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
            raise

    async def resume_from_crash(self) -> None:
        """Restore controller state from persisted database after crash/restart and validate invariants."""
        logger.info("Resuming MESA-QA Controller for run %s from persisted state...", self.run_id)
        await self.controller_db.initialize()
        await self.oracle_db.initialize()

        state = await self.controller_db.get_run_state(self.run_id)
        if not state:
            raise RuntimeError(f"Cannot resume run '{self.run_id}': no recorded state in database.")

        try:
            # Restore and validate metadata
            self._started_at = state["started_at"]
            self._epoch = state.get("current_epoch", 0)
            self._action_count = state.get("action_count", 0)
            self.tester.thread_id = state.get("tester_thread_id")

            candidate_wt_str = state.get("candidate_worktree")
            if not candidate_wt_str:
                raise RuntimeError("Cannot resume run: missing candidate_worktree in persisted state.")
            candidate_wt = Path(candidate_wt_str)
            if not candidate_wt.exists():
                raise FileNotFoundError(f"Cannot resume run: candidate worktree does not exist at {candidate_wt}")

            # Validate candidate worktree hygiene and branch
            self.process_mgr.worktree_mgr.check_main_hygiene()
            persisted_baseline = (
                self.evidence_store.read_json("main_baseline.json")
                or (json.loads(state.get("baseline_main_json")) if state.get("baseline_main_json") else None)
            )
            if not persisted_baseline:
                persisted_head = state.get("baseline_main_head")
                if persisted_head:
                    persisted_baseline = {"head": persisted_head}
                else:
                    raise RuntimeError("Cannot resume run: missing persisted original MESA baseline.")

            # Validate that original MESA did not change while controller was stopped/crashed
            self.process_mgr.worktree_mgr.assert_main_unchanged(persisted_baseline)
            self._main_baseline = persisted_baseline

            persisted_head = state.get("candidate_head")
            actual_head = self.process_mgr.worktree_mgr._run_git(candidate_wt, ["rev-parse", "HEAD"]).strip()
            if persisted_head and actual_head != persisted_head:
                raise RuntimeError(
                    f"Candidate HEAD mismatch on resume: expected {persisted_head}, got {actual_head}"
                )

            self.process_mgr.candidate_worktree = candidate_wt
            self.process_mgr.candidate_branch = state.get("candidate_branch")
            self.process_mgr.candidate_base_sha = state.get("candidate_base_sha")

            # Restore Scenario Engine
            seed = state.get("scenario_seed") or self.config.run.seed
            self.scenario_engine.seed = seed
            self.scenario_engine.load_suite()
            persisted_cursor = state.get("scenario_cursor", 0)
            self.scenario_engine.cursor = persisted_cursor

            # Restore runtime and MCP binding
            await self._set_state(State.START_MESA)
            await self.process_mgr.start_all()
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)

            await self._set_state(State.START_MCP)
            gateway_url = (
                self.process_mgr.mcp_gateway.gateway_url
                if self.process_mgr.mcp_gateway
                else f"http://127.0.0.1:{self.config.mesa.gateway_port}"
            )
            bootstrap = MESABootstrap(
                candidate_worktree=candidate_wt,
                python_bin=self.config.mesa.python_path,
                control_db_path=self.run_dir / "gateway-control.db",
                gateway_url=gateway_url,
            )
            tester_ws = self.run_dir / "tester_workspace"
            binding = bootstrap.prepare_tester_workspace(tester_ws)
            self.tester.configure_mesa_launcher(
                binding["launcher_prefix"],
                gateway_url=gateway_url,
            )
            binding_context = dict(binding["binding_context"])
            gateway_credential = binding_context.pop("gateway_credential")
            self._binding_context = binding_context
            if self.config.approval.enabled:
                status_client = MCPStatusClient(
                    gateway_url=gateway_url,
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

            await self._set_state(State.RUNNING)
            logger.info(
                "MESA-QA Controller crash resume complete. Resumed at epoch %d, cursor %d, action_count %d.",
                self._epoch,
                self.scenario_engine.cursor,
                self._action_count,
            )
        except Exception as exc:
            logger.exception("Fatal error during resume: %s", exc)
            if self.state_machine.current != State.FAILED:
                await self._set_state(State.FAILED)
            await self.process_mgr.stop_all()
            if self._main_baseline is not None:
                self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
            raise

    def _get_owned_process_pids(self) -> List[int]:
        """Collect all active owned subprocess root PIDs (MESA runtime, MCP gateway, Tester Codex, Repair Codex)."""
        pids: List[int] = []
        if (
            self.process_mgr.mesa_runtime
            and self.process_mgr.mesa_runtime._process
            and self.process_mgr.mesa_runtime._process.pid
        ):
            pids.append(self.process_mgr.mesa_runtime._process.pid)
        if (
            self.process_mgr.mcp_gateway
            and self.process_mgr.mcp_gateway._process
            and self.process_mgr.mcp_gateway._process.pid
        ):
            pids.append(self.process_mgr.mcp_gateway._process.pid)
        if (
            self.tester
            and getattr(self.tester, "runner", None)
            and getattr(self.tester.runner, "current_process_pid", None)
        ):
            pids.append(self.tester.runner.current_process_pid)
        if (
            getattr(self, "repair_gate", None)
            and getattr(self.repair_gate, "repairer", None)
            and getattr(self.repair_gate.repairer, "runner", None)
            and getattr(self.repair_gate.repairer.runner, "current_process_pid", None)
        ):
            pids.append(self.repair_gate.repairer.runner.current_process_pid)
        return pids

    async def _resource_monitor_loop(self) -> None:
        """Background monitor checking process tree RSS against hard limits every resources.sample_seconds."""
        sample_interval = max(0.5, float(self.config.resources.sample_seconds))
        while not self._stop_requested:
            try:
                pids = self._get_owned_process_pids()
                metrics = self.sampler.sample_process_trees(pids)
                if metrics.get("hard_limit_exceeded"):
                    logger.error(
                        "Process trees exceeded resource hard-stop limit (%s MB > %s MB). Initiating emergency stop...",
                        metrics.get("rss_mb"),
                        self.config.resources.hard_stop_rss_mb,
                    )
                    self._stop_requested = True
                    self.evidence_store.append_json_record(
                        "resource_breach.json",
                        {
                            "run_id": self.run_id,
                            "rss_mb": metrics.get("rss_mb"),
                            "hard_stop_rss_mb": self.config.resources.hard_stop_rss_mb,
                            "num_processes": metrics.get("num_processes"),
                            "pids_monitored": pids,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    if self.state_machine.current not in (State.STOPPING, State.COMPLETED, State.FAILED):
                        await self._set_state(State.STOPPING)
                    await self.cancel_active_action()
                    await self.process_mgr.stop_all()
                    break
            except Exception as exc:
                logger.warning("Error in background resource monitor loop: %s", exc)

            try:
                await asyncio.sleep(sample_interval)
            except asyncio.CancelledError:
                break

    async def _watch_control_during_action(self, action_task: asyncio.Task[Any]) -> None:
        """Lightweight local control watcher polling during active actions."""
        while not action_task.done():
            await asyncio.sleep(0.15)
            if action_task.done():
                break
            try:
                ctrl = await self.controller_db.get_control(self.run_id)
            except Exception as e:
                logger.debug("Error checking control db during active action: %s", e)
                continue

            if ctrl == "stop":
                logger.info("Emergency STOP requested via control DB during active action!")
                self._stop_requested = True
                await self.controller_db.clear_control(self.run_id)
                self.evidence_store.append_json_record(
                    "emergency_stop.json",
                    {
                        "run_id": self.run_id,
                        "action": "stop",
                        "active_action_cancelled": True,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                if self.state_machine.current not in (State.STOPPING, State.COMPLETED, State.FAILED):
                    await self._set_state(State.STOPPING)
                if not action_task.done():
                    action_task.cancel()
                break
            elif ctrl == "pause":
                logger.info("Pause requested via control DB during active action.")
                self._pause_requested = True
                await self.controller_db.clear_control(self.run_id)

    async def run_loop(self) -> None:
        logger.info(
            "Starting endurance test loop (duration: %s hours)...",
            self.config.run.duration_hours,
        )
        start_time = time.time()
        max_duration_sec = self.config.run.duration_hours * 3600

        self._resource_monitor_task = asyncio.create_task(self._resource_monitor_loop())

        try:
            while time.time() - start_time < max_duration_sec:
                control = await self.controller_db.get_control(self.run_id)
                if control == "pause":
                    self._pause_requested = True
                    await self.controller_db.clear_control(self.run_id)
                elif control == "resume":
                    self._pause_requested = False
                    await self.controller_db.clear_control(self.run_id)
                elif control == "stop":
                    self._stop_requested = True
                    await self.controller_db.clear_control(self.run_id)

                if self.state_machine.current == State.WAITING_FOR_CODEX:
                    if control == "resume":
                        await self._set_state(State.RUNNING)
                    elif control == "stop" or self._stop_requested:
                        break
                    else:
                        await asyncio.sleep(1.0)
                        continue

                if self._stop_requested:
                    logger.info("Stop requested. Exiting endurance loop.")
                    break

                if self._pause_requested:
                    await self._set_state(State.PAUSED)
                    logger.info("Controller PAUSED. Waiting for resume...")
                    while self._pause_requested and not self._stop_requested:
                        await asyncio.sleep(0.5)
                        pause_ctrl = await self.controller_db.get_control(self.run_id)
                        if pause_ctrl == "resume":
                            self._pause_requested = False
                            await self.controller_db.clear_control(self.run_id)
                            break
                        elif pause_ctrl == "stop":
                            self._stop_requested = True
                            await self.controller_db.clear_control(self.run_id)
                            break

                    if not self._stop_requested:
                        await self._set_state(State.RUNNING)
                    else:
                        break

                # Check resources
                pids = self._get_owned_process_pids()
                self.sampler.sample_process_trees(pids)

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

                action_task = asyncio.create_task(self._process_event(event))
                self._current_action_task = action_task
                watcher_task = asyncio.create_task(
                    self._watch_control_during_action(action_task)
                )
                try:
                    await action_task
                except asyncio.CancelledError:
                    logger.info("Active action cancelled by controller.")
                    break
                finally:
                    if not watcher_task.done():
                        watcher_task.cancel()
                        try:
                            await watcher_task
                        except asyncio.CancelledError:
                            pass
                    self._current_action_task = None

                # Cadence sleep
                cadence = self._rng.uniform(
                    self.config.run.cadence_seconds_min, self.config.run.cadence_seconds_max
                )
                await asyncio.sleep(cadence)

            await self._set_state(State.STOPPING)
            await self.shutdown()
            await self._set_state(State.COMPLETED)
            logger.info("Endurance run completed.")
        except Exception as exc:
            logger.exception("Fatal error in run loop: %s", exc)
            if self.state_machine.current != State.FAILED:
                await self._set_state(State.FAILED)
            await self.process_mgr.stop_all()
            if self._main_baseline is not None:
                self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)
            raise
        finally:
            if self._resource_monitor_task and not self._resource_monitor_task.done():
                self._resource_monitor_task.cancel()
                try:
                    await self._resource_monitor_task
                except asyncio.CancelledError:
                    pass
                self._resource_monitor_task = None

    async def _process_event(self, event: ScenarioEvent) -> None:
        self._action_count += 1
        action_id = f"act_{self.run_id}_{self._action_count:06d}"
        template_id = event.template_id or event.id
        runtime_event_id = (
            f"ep{self._epoch}_{template_id}"
            if self._epoch > 0
            else template_id
        )
        event = event.model_copy(
            update={
                "id": runtime_event_id,
                "template_id": template_id,
                "epoch": self._epoch,
                "idempotency_key": event.idempotency_key
                or f"qa:{self.run_id}:{action_id}:1",
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
            self._rotation_pending_action_id = action_id
            await self.controller_db.record_action(
                action_id=action_id,
                run_id=self.run_id,
                scenario_event_id=event.id,
                action_type=event.kind.value,
                request=event.model_dump(),
                response={"thread_rotated": True, "old_thread_id": old_thread, "status": "PENDING"},
                verdict="PENDING",
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        # 2. Execute action via Tester Codex
        tester_ws = self.run_dir / "tester_workspace"
        env = os.environ.copy()
        obs = await self.tester.execute_action(event, action_id, tester_ws, mcp_env=env)

        if obs.tester_assessment == "infra_error":
            if self._rotation_pending_old_thread is not None:
                old_thread = self._rotation_pending_old_thread
                pending_action_id = self._rotation_pending_action_id
                new_thread = self.tester.thread_id
                rotation = {
                    "run_id": self.run_id,
                    "scenario_event_id": event.id,
                    "old_thread_id": old_thread,
                    "new_thread_id": new_thread,
                    "recall_verdict": "FAIL",
                    "status": "FAIL",
                    "reason": f"Post-rotation action failed with infra_error: {obs.reason}",
                }
                self.evidence_store.append_json_record("thread_rotation.json", rotation)
                if pending_action_id:
                    await self.controller_db.update_action_verdict(
                        pending_action_id,
                        verdict="FAIL",
                        response={
                            "thread_rotated": True,
                            "old_thread_id": old_thread,
                            "new_thread_id": new_thread,
                            "status": "FAIL",
                            "reason": rotation["reason"],
                        },
                    )
                self._rotation_pending_old_thread = None
                self._rotation_pending_action_id = None

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
            await self._set_state(State.WAITING_FOR_CODEX)
            return

        if event.kind.value in _WRITE_TOOL_BY_KIND:
            obs = await self._finalize_write(event, obs)
            if str(obs.actual.get("operation_state", "")).upper() == "COMMITTED":
                # Ground truth advances only after real MESA durable success.
                await self.oracle_db.apply_event(event)

        # 3. Judge output against Oracle
        verdict = await self.judge.judge(event, obs, self.oracle_eval)

        # Hard evidence gate for thread rotation
        if self._rotation_pending_old_thread is not None:
            old_thread = self._rotation_pending_old_thread
            pending_action_id = self._rotation_pending_action_id
            new_thread = self.tester.thread_id
            mcp_tool_verified = (
                bool(obs.tools_called and "mesa_recall" in obs.tools_called)
                if event.kind.value == "recall"
                else bool(obs.tools_called)
            )
            rotation_pass = bool(
                old_thread is not None
                and new_thread is not None
                and old_thread != new_thread
                and verdict.is_pass
                and mcp_tool_verified
            )
            rotation_verdict = "PASS" if rotation_pass else "FAIL"
            rotation = {
                "run_id": self.run_id,
                "scenario_event_id": event.id,
                "old_thread_id": old_thread,
                "new_thread_id": new_thread,
                "recall_verdict": "PASS" if verdict.is_pass else "FAIL",
                "mcp_tool_verified": mcp_tool_verified,
                "status": rotation_verdict,
                "reason": (
                    "Old thread dropped, new distinct thread established, and fresh recall succeeded via MESA MCP"
                    if rotation_pass
                    else (
                        f"Rotation gate check failed: old_thread={old_thread!r}, "
                        f"new_thread={new_thread!r}, verdict_pass={verdict.is_pass}, "
                        f"mcp_verified={mcp_tool_verified}"
                    )
                ),
            }
            self.evidence_store.append_json_record("thread_rotation.json", rotation)
            if pending_action_id:
                await self.controller_db.update_action_verdict(
                    pending_action_id,
                    verdict=rotation_verdict,
                    response={
                        "thread_rotated": True,
                        "old_thread_id": old_thread,
                        "new_thread_id": new_thread,
                        "status": rotation_verdict,
                        "reason": rotation["reason"],
                    },
                )
            self._rotation_pending_old_thread = None
            self._rotation_pending_action_id = None
            if not rotation_pass:
                verdict = Verdict(
                    is_pass=False,
                    is_candidate_anomaly=True,
                    category="THREAD_ROTATION_GATE_FAILURE",
                    reason=rotation["reason"],
                    expected=event.expected,
                    actual=obs.actual,
                )

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
                "error": lifecycle.error,
            }
        )
        assessment = observation.tester_assessment
        reason = observation.reason
        if lifecycle.outcome != "PASS":
            error_dict = lifecycle.error if isinstance(lifecycle.error, dict) else {}
            err_code = str(error_dict.get("code", "")).lower()
            err_msg = str(error_dict.get("message", "")).lower()
            if "provider" in err_code or "rate_limit" in err_code or "provider" in err_msg or "rate limit" in err_msg or lifecycle.final_status == "TIMEOUT":
                assessment = "infra_error"
                reason = f"operation failed with infrastructure issue ({lifecycle.final_status}): {lifecycle.error or 'unavailable'}"
            elif lifecycle.final_status in {"REJECTED", "DENIED"} or "policy" in err_code or "policy" in err_msg:
                assessment = "policy_rejection"
                reason = f"operation policy rejection ({lifecycle.final_status}): {lifecycle.error or 'policy rejected'}"
            else:
                assessment = "candidate_anomaly"
                reason = f"operation candidate anomaly with terminal state {lifecycle.final_status}: {lifecycle.error}"
        return observation.model_copy(
            update={
                "actual": actual,
                "tester_assessment": assessment,
                "reason": reason,
                "needs_recheck": assessment == "infra_error",
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
        await self._set_state(State.ANOMALY)
        logger.warning(
            "Candidate anomaly detected for event %s: %s", event.id, verdict.reason
        )

        # Step 1: Recheck
        await self._set_state(State.RECHECKING)
        await asyncio.sleep(3.0)  # Bounded stabilization interval

        # Determine explicit reproduction strategy
        if (
            event.idempotency_strategy == "reuse_same_key"
            or event.kind == ActionKind.IDEMPOTENCY
        ):
            repro_strategy = "reuse_same_key"
            recheck_event = event
        else:
            repro_strategy = "fresh_attempt"
            recheck_action_id = f"recheck_{obs.action_id}"
            recheck_event = event.model_copy(
                update={
                    "idempotency_key": f"qa:{self.run_id}:{recheck_action_id}:2"
                    if event.idempotency_key
                    else None
                }
            )

        # Step 2: Reproduce
        await self._set_state(State.REPRODUCING)
        recheck_action_id = f"recheck_{obs.action_id}"
        recheck_obs = await self.tester.execute_action(
            recheck_event, recheck_action_id, self.run_dir / "tester_workspace"
        )
        recheck_verdict = await self.judge.judge(
            recheck_event, recheck_obs, self.oracle_eval
        )

        if not recheck_verdict.is_candidate_anomaly:
            logger.info(
                "Anomaly did not reproduce on recheck (strategy=%s). Dismissing transient anomaly.",
                repro_strategy,
            )
            await self._set_state(State.RUNNING)
            return

        # Step 3: Confirmed Bug
        await self._set_state(State.CONFIRMED_BUG)
        severity, category = self.classifier.classify(verdict, event.kind.value)
        bug_id = f"BUG-{len(self._bugs)+1:04d}"

        candidate_worktree = self.process_mgr.candidate_worktree
        candidate_head = str(self.process_mgr.candidate_branch or "")
        if candidate_worktree and candidate_worktree.exists():
            try:
                candidate_head = self.process_mgr.worktree_mgr._run_git(
                    candidate_worktree, ["rev-parse", "HEAD"]
                ).strip()
            except Exception:
                logger.exception("Could not resolve candidate SHA for %s", bug_id)

        bug = BugReport(
            bug_id=bug_id,
            run_id=self.run_id,
            severity=severity,
            category=category,
            scenario_id=event.id,
            reproduction_strategy=repro_strategy,
            preconditions={"reproduction_strategy": repro_strategy},
            steps=[event.model_dump(), recheck_event.model_dump()],
            expected={"expected": verdict.expected},
            actual={"actual": verdict.actual},
            repeat_count=2,
            candidate_commit_before=candidate_head,
        )

        repro_spec: Dict[str, Any] = {}
        if candidate_worktree and candidate_worktree.exists():
            try:
                regression_path, spec_path, repro_spec = self.reproducer.materialize(
                    bug=bug,
                    event=recheck_event,
                    candidate_worktree=candidate_worktree,
                    tester_workspace=self.run_dir / "tester_workspace",
                    launcher_prefix=self.tester.launcher_prefix,
                    codex_binary=self.config.codex.binary,
                    gateway_url=self.tester.gateway_url,
                    timeout_seconds=self.config.codex.tester_timeout_seconds,
                )
                bug.preconditions.update(
                    {
                        "pre_fix_test_file": regression_path,
                        "reproduction_spec": str(spec_path),
                        "reproduction_command": repro_spec["reproduction_command"],
                    }
                )
            except Exception as exc:
                bug.preconditions["reproduction_error"] = str(exc)
                logger.exception("Could not materialize production reproduction for %s", bug_id)

        repro_execution_data = {
            "status": "CONFIRMED_ANOMALY",
            "reproduced": True,
            "strategy": repro_strategy,
            "candidate_commit": candidate_head,
            "recheck_action_id": recheck_action_id,
            "first_verdict": {
                "category": verdict.category,
                "reason": verdict.reason,
                "expected": verdict.expected,
                "actual": verdict.actual,
            },
            "recheck_verdict": {
                "category": recheck_verdict.category,
                "reason": recheck_verdict.reason,
                "expected": recheck_verdict.expected,
                "actual": recheck_verdict.actual,
            },
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        if repro_spec:
            repro_execution_data.update(
                {
                    "candidate_worktree": str(candidate_worktree),
                    "reproduction_spec": str(bug.preconditions["reproduction_spec"]),
                    "required_mcp_tool": repro_spec["required_mcp_tool"],
                    "actual_reproduction_command": repro_spec["reproduction_command"],
                    "regression_path": bug.preconditions["pre_fix_test_file"],
                    "regression_command": [
                        str(self.config.mesa.python_path),
                        "-m",
                        "pytest",
                        bug.preconditions["pre_fix_test_file"],
                    ],
                }
            )

        # Create Evidence Bundle
        bundle_dir = self.evidence_store.create_bundle(
            bug=bug,
            user_sequence=[event.model_dump(), recheck_event.model_dump()],
            expected_data={"expected": verdict.expected},
            actual_data={"actual": verdict.actual},
            repro_execution=repro_execution_data,
        )
        bug.preconditions["bundle_dir"] = str(bundle_dir)
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
            await self._set_state(State.RUNNING)

    async def _execute_repair_pipeline(
        self, bug: BugReport, event: ScenarioEvent
    ) -> None:
        await self._set_state(State.REPAIRING)
        logger.info("Starting autonomous repair pipeline for bug %s...", bug.bug_id)

        try:
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
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "NEEDS_REVIEW",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            # Verify genuine PRE-FIX FAIL
            pre_fix_fail_ok, output = self.repair_verifier.verify_pre_fix_failure(
                self.process_mgr.candidate_worktree, test_file
            )
            if not pre_fix_fail_ok:
                logger.warning(
                    "PRE-FIX FAIL check failed on %s: %s. Aborting repair.",
                    test_file,
                    output,
                )
                self.evidence_store.append_json_record(
                    "repair_gate.json",
                    {
                        "run_id": self.run_id,
                        "bug_id": bug.bug_id,
                        "gate": "PRE_FIX_FAIL",
                        "status": "FAIL",
                        "test_file": test_file,
                        "reason": output,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "NEEDS_REVIEW",
                        "reason": f"pre-fix fail check failed: {output[:100]}",
                    }
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "NEEDS_REVIEW",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            logger.info("Genuine PRE-FIX FAIL verified on %s.", test_file)

            # Evaluate Gates G1-G5
            gate_ok, gate_reason = self.repair_gate.evaluate_gates(
                bug=bug,
                candidate_worktree=self.process_mgr.candidate_worktree,
                stable_reproduction_proven=bug.repeat_count >= 2,
                pre_fix_test_exists=True,
            )

            if not gate_ok:
                logger.warning("Repair gate rejected repair: %s", gate_reason)
                self.evidence_store.append_json_record(
                    "repair_gate.json",
                    {
                        "run_id": self.run_id,
                        "bug_id": bug.bug_id,
                        "gate": "GATES_EVALUATION",
                        "status": "FAIL",
                        "reason": gate_reason,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "REPAIR_GATE_REJECTED",
                        "reason": gate_reason,
                    }
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "REPAIR_GATE_REJECTED",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            # Capture pre-repair candidate snapshot & verify main repository integrity
            pre_repair_snapshot = (
                self.process_mgr.worktree_mgr.capture_candidate_snapshot(
                    self.process_mgr.candidate_worktree
                )
            )
            bug.preconditions["pre_repair_snapshot"] = pre_repair_snapshot
            self.evidence_store.save_json(
                f"pre_repair_snapshot_{bug.bug_id}.json", pre_repair_snapshot
            )
            self.process_mgr.worktree_mgr.assert_main_unchanged(
                pre_repair_snapshot["main_baseline"]
            )

            # Pre-repair candidate identity hard gate
            self.process_mgr.worktree_mgr.assert_candidate_identity(
                self.process_mgr.candidate_worktree,
                baseline_commit=self.process_mgr.candidate_base_sha,
                main_baseline=pre_repair_snapshot["main_baseline"],
            )

            # Invoke Repairer Codex
            await self._set_state(State.VERIFYING)
            repair_res = await self.repairer.execute_repair(
                bug=bug,
                candidate_worktree=self.process_mgr.candidate_worktree,
                evidence_summary=f"Bug ID: {bug.bug_id}\nExpected: {bug.expected}\nActual: {bug.actual}",
            )

            # Post-repair candidate identity hard gate & snapshot
            self.process_mgr.worktree_mgr.assert_candidate_identity(
                self.process_mgr.candidate_worktree,
                baseline_commit=self.process_mgr.candidate_base_sha,
                main_baseline=pre_repair_snapshot["main_baseline"],
            )
            post_repair_snapshot = (
                self.process_mgr.worktree_mgr.capture_candidate_snapshot(
                    self.process_mgr.candidate_worktree
                )
            )
            self.evidence_store.save_json(
                f"post_repair_snapshot_{bug.bug_id}.json", post_repair_snapshot
            )

            # Authoritative Gate: Repairer structured success
            if not repair_res.success:
                logger.warning(
                    "Repairer reported success=false for bug %s (%s). Commit blocked.",
                    bug.bug_id,
                    repair_res.error_message or "repair failed",
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "REPAIR_FAILED",
                        "reason": f"Repairer reported success=false: {repair_res.error_message or 'repair failed'}",
                    }
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "REPAIR_FAILED",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            # Enforce post-repair bounded diff safety policy
            diff_ok, diff_reason = self.policy_guard.validate_diff(
                self.process_mgr.candidate_worktree
            )
            if not diff_ok:
                logger.warning(
                    "Repair diff violates safety policy for bug %s: %s. Discarding uncommitted candidate changes.",
                    bug.bug_id,
                    diff_reason,
                )
                self.policy_guard.discard_changes(self.process_mgr.candidate_worktree)
                self.evidence_store.append_json_record(
                    "repair_policy_violations.json",
                    {
                        "run_id": self.run_id,
                        "bug_id": bug.bug_id,
                        "status": "POLICY_VIOLATION",
                        "reason": diff_reason,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "POLICY_VIOLATION",
                        "reason": diff_reason,
                    }
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "POLICY_VIOLATION",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            # Verify POST-FIX PASS (genuine regression test)
            post_fix_pass, post_fix_out = self.repair_verifier.run_pytest_on_file(
                self.process_mgr.candidate_worktree, test_file
            )

            if not post_fix_pass:
                logger.warning(
                    "POST-FIX PASS verification failed for bug %s: %s",
                    bug.bug_id,
                    post_fix_out[:200],
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "NEEDS_REVIEW",
                        "reason": f"post-fix test failed: {post_fix_out[:100]}",
                    }
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "NEEDS_REVIEW",
                    datetime.now(timezone.utc).isoformat(),
                )
                await self._set_state(State.RUNNING)
                return

            approved_paths = self.policy_guard.changed_paths(
                self.process_mgr.candidate_worktree
            )

            # Run relevant targeted tests
            targeted_tests = self.repair_verifier.find_targeted_tests(
                self.process_mgr.candidate_worktree, approved_paths
            )
            if targeted_tests:
                targeted_pass, targeted_out = self.repair_verifier.run_targeted_tests(
                    self.process_mgr.candidate_worktree, targeted_tests
                )
                repair_res.targeted_tests_run = targeted_tests
                repair_res.targeted_tests_passed = targeted_pass
                if not targeted_pass:
                    logger.warning(
                        "Targeted tests verification failed for bug %s: %s",
                        bug.bug_id,
                        targeted_out[:200],
                    )
                    self._repairs.append(
                        {
                            "bug_id": bug.bug_id,
                            "status": "TARGETED_TESTS_FAILED",
                            "reason": f"targeted tests failed: {targeted_out[:100]}",
                        }
                    )
                    await self.controller_db.record_bug(
                        bug.bug_id,
                        self.run_id,
                        bug.severity.value,
                        bug.category,
                        bug.model_dump(),
                        "TARGETED_TESTS_FAILED",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    await self._set_state(State.RUNNING)
                    return

            # Run full test suite if configured
            if self.config.verification.run_full_suite:
                full_pass, full_out = self.repair_verifier.run_full_suite(
                    self.process_mgr.candidate_worktree
                )
                if not full_pass:
                    logger.warning(
                        "Full test suite verification failed for bug %s: %s",
                        bug.bug_id,
                        full_out[:200],
                    )
                    self._repairs.append(
                        {
                            "bug_id": bug.bug_id,
                            "status": "FULL_SUITE_FAILED",
                            "reason": f"full suite failed: {full_out[:100]}",
                        }
                    )
                    await self.controller_db.record_bug(
                        bug.bug_id,
                        self.run_id,
                        bug.severity.value,
                        bug.category,
                        bug.model_dump(),
                        "FULL_SUITE_FAILED",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    await self._set_state(State.RUNNING)
                    return

            sha = self.repair_verifier.commit_repair(
                self.process_mgr.candidate_worktree,
                bug.bug_id,
                bug.category,
                approved_paths,
            )
            repair_res.post_fix_test_passed = True
            repair_res.commit_sha = sha

            # Step 5: Restart candidate runtime and live repro
            await self._set_state(State.RESTARTING)
            await self.process_mgr.restart_all()

            await self._set_state(State.LIVE_RECHECK)
            live_obs = await self.tester.execute_action(
                event, f"live_{bug.bug_id}", self.run_dir / "tester_workspace"
            )
            live_verdict = await self.judge.judge(event, live_obs, self.oracle_eval)
            repair_res.live_repro_passed = bool(live_verdict.is_pass)

            if live_verdict.is_pass:
                logger.info(
                    "LIVE REPRO PASSED! Bug %s resolved and verified on candidate runtime.",
                    bug.bug_id,
                )
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "VERIFIED",
                    datetime.now(timezone.utc).isoformat(),
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "VERIFIED",
                        "commit_sha": sha,
                        "live_repro_passed": True,
                    }
                )
            else:
                logger.warning("Live repro failed post-restart for bug %s.", bug.bug_id)
                await self.controller_db.record_bug(
                    bug.bug_id,
                    self.run_id,
                    bug.severity.value,
                    bug.category,
                    bug.model_dump(),
                    "LIVE_REPRO_FAILED",
                    datetime.now(timezone.utc).isoformat(),
                )
                self._repairs.append(
                    {
                        "bug_id": bug.bug_id,
                        "status": "LIVE_REPRO_FAILED",
                        "commit_sha": sha,
                        "live_repro_passed": False,
                    }
                )

            await self._set_state(State.RUNNING)

        except Exception as exc:
            logger.exception("Unexpected error in repair pipeline for bug %s: %s", bug.bug_id, exc)
            self._repairs.append(
                {
                    "bug_id": bug.bug_id,
                    "status": "REPAIR_FAILED",
                    "reason": f"unexpected error: {exc}",
                }
            )
            await self.controller_db.record_bug(
                bug.bug_id,
                self.run_id,
                bug.severity.value,
                bug.category,
                bug.model_dump(),
                "REPAIR_FAILED",
                datetime.now(timezone.utc).isoformat(),
            )
            await self._set_state(State.RUNNING)

    def pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False

    async def cancel_active_action(self) -> None:
        """Emergency cancellation of currently executing action, codex runner or approval poll."""
        if self._current_action_task and not self._current_action_task.done():
            logger.info("Emergency cancelling current active action task...")
            self._current_action_task.cancel()
            try:
                await self._current_action_task
            except asyncio.CancelledError:
                pass
            self._current_action_task = None

    async def stop(self) -> None:
        self._stop_requested = True
        if self._resource_monitor_task and not self._resource_monitor_task.done():
            self._resource_monitor_task.cancel()
        await self.cancel_active_action()
        if self.state_machine.current not in (State.STOPPING, State.COMPLETED, State.FAILED):
            await self._set_state(State.STOPPING)
        await self.shutdown()
        if self.state_machine.current != State.COMPLETED:
            await self._set_state(State.COMPLETED)

    async def shutdown(self) -> None:
        logger.info("Shutting down MESA-QA processes gracefully...")
        try:
            await self.process_mgr.stop_all()
        except Exception as e:
            logger.warning("Error stopping processes during shutdown: %s", e)

        if getattr(self, "_rotation_pending_old_thread", None) is not None:
            pending_action_id = getattr(self, "_rotation_pending_action_id", None)
            rotation = {
                "run_id": self.run_id,
                "old_thread_id": self._rotation_pending_old_thread,
                "new_thread_id": self.tester.thread_id,
                "status": "FAIL",
                "reason": "Run ended while thread rotation was still PENDING without subsequent verification",
            }
            self.evidence_store.append_json_record("thread_rotation.json", rotation)
            if pending_action_id:
                await self.controller_db.update_action_verdict(
                    pending_action_id,
                    verdict="FAIL",
                    response={
                        "thread_rotated": True,
                        "old_thread_id": self._rotation_pending_old_thread,
                        "status": "FAIL",
                        "reason": rotation["reason"],
                    },
                )
            self._rotation_pending_old_thread = None
            self._rotation_pending_action_id = None

        # Save final reports
        try:
            state_dict = await self.controller_db.get_run_state(self.run_id) or {
                "run_id": self.run_id,
                "status": self.state_machine.current.value,
            }
            self.report_builder.generate_final_report(state_dict, self._bugs, self._repairs)
        except Exception as e:
            logger.warning("Error generating final report during shutdown: %s", e)

        if self._main_baseline is not None:
            self.process_mgr.worktree_mgr.assert_main_unchanged(self._main_baseline)

        logger.info("Shutdown complete.")
