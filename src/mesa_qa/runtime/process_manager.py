from __future__ import annotations

import base64
import logging
import secrets
from pathlib import Path
from typing import Optional

from mesa_qa.config import QAConfig
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.runtime.mesa_runtime import MesaCandidateRuntime
from mesa_qa.runtime.mcp_gateway import MesaMCPGatewayProcess
from mesa_qa.storage.paths import assert_safe_paths, discover_normal_mesa_storage

logger = logging.getLogger("mesa_qa.process_manager")


class ProcessManager:
    def __init__(self, config: QAConfig, run_dir: Path):
        self.config = config
        self.run_dir = run_dir
        self.worktree_mgr = WorktreeManager(
            main_repo=config.mesa.repo_path,
            candidate_root=config.candidate.worktree_root,
            branch_prefix=config.candidate.branch_prefix,
        )
        self.candidate_worktree: Optional[Path] = None
        self.candidate_branch: Optional[str] = None
        self.candidate_base_sha: Optional[str] = None
        self.mesa_runtime: Optional[MesaCandidateRuntime] = None
        self.mcp_gateway: Optional[MesaMCPGatewayProcess] = None

        # Per-run ephemeral credentials
        self.api_key: str = secrets.token_urlsafe(32)
        self.encryption_key: str = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        self.principal_id: str = f"qa-service-principal-{secrets.token_hex(6)}"

    def setup_worktree(
        self,
        run_id: str,
        candidate_ref: Optional[str] = None,
        baseline_commit: Optional[str] = None,
    ) -> Path:
        target_ref = candidate_ref or self.config.mesa.candidate_ref
        self.candidate_worktree, self.candidate_branch, self.candidate_base_sha = (
            self.worktree_mgr.create_candidate_worktree(
                run_id=run_id,
                candidate_ref=target_ref,
                baseline_commit=baseline_commit,
            )
        )
        qa_storage = self.run_dir / "mesa-storage"
        assert_safe_paths(
            main_repo=self.config.mesa.repo_path,
            candidate_worktree=self.candidate_worktree,
            qa_storage=qa_storage,
            normal_mesa_storage=self.config.mesa.normal_storage_root
            or discover_normal_mesa_storage(self.config.mesa.repo_path),
            qa_root=self.run_dir.parent.parent,
        )
        return self.candidate_worktree

    async def start_all(self) -> None:
        if not self.candidate_worktree:
            raise RuntimeError("Candidate worktree is not set up!")

        qa_storage = self.run_dir / "mesa-storage"
        logs_dir = self.run_dir / "logs"
        control_db = self.run_dir / "gateway-control.db"

        # 1. Mesa Candidate Runtime
        self.mesa_runtime = MesaCandidateRuntime(
            candidate_worktree=self.candidate_worktree,
            python_bin=self.config.mesa.python_path,
            storage_root=qa_storage,
            port=self.config.mesa.port,
            api_key=self.api_key,
            principal_id=self.principal_id,
            runtime_profile=self.config.mesa.runtime_profile,
            model_enabled=self.config.mesa.model_enabled,
            external_provider_enabled=self.config.mesa.external_provider_enabled,
            llm_provider=self.config.mesa.llm_provider,
            validation_mode=self.config.mesa.validation_mode,
            log_file=logs_dir / "mesa.log",
        )
        await self.mesa_runtime.start()
        if not await self.mesa_runtime.wait_until_ready(timeout_seconds=45.0):
            raise RuntimeError("Failed to start MESA candidate runtime")

        # 2. Mesa MCP Gateway
        self.mcp_gateway = MesaMCPGatewayProcess(
            candidate_worktree=self.candidate_worktree,
            python_bin=self.config.mesa.python_path,
            control_db_path=control_db,
            gateway_port=self.config.mesa.gateway_port,
            mesa_api_url=self.mesa_runtime.base_url,
            mesa_api_key=self.api_key,
            encryption_key=self.encryption_key,
            log_file=logs_dir / "mcp_gateway.log",
        )
        await self.mcp_gateway.start()
        if not await self.mcp_gateway.wait_until_ready(timeout_seconds=30.0):
            raise RuntimeError("Failed to start MESA MCP Gateway")

    async def restart_all(self) -> None:
        logger.info("Restarting MESA candidate services from worktree %s...", self.candidate_worktree)
        if not self.candidate_worktree:
            raise RuntimeError("Candidate worktree is not set up!")

        qa_storage = self.run_dir / "mesa-storage"
        assert_safe_paths(
            main_repo=self.config.mesa.repo_path,
            candidate_worktree=self.candidate_worktree,
            qa_storage=qa_storage,
            normal_mesa_storage=self.config.mesa.normal_storage_root
            or discover_normal_mesa_storage(self.config.mesa.repo_path),
            qa_root=self.run_dir.parent.parent,
        )

        await self.stop_all()
        await self.start_all()
        logger.info("MESA candidate services successfully restarted.")

    async def stop_all(self) -> None:
        if self.mcp_gateway:
            await self.mcp_gateway.stop()
        if self.mesa_runtime:
            await self.mesa_runtime.stop()

    def teardown(self, delete_worktree: bool = True, delete_branch: bool = False) -> None:
        if delete_worktree and self.candidate_worktree:
            self.worktree_mgr.remove_candidate_worktree(
                self.candidate_worktree,
                delete_branch=delete_branch,
                branch_name=self.candidate_branch,
            )
            self.candidate_worktree = None

    async def async_teardown(self, delete_worktree: bool = True, delete_branch: bool = False) -> None:
        logger.info("Executing teardown of MESA candidate services and resources...")
        await self.stop_all()
        self.teardown(delete_worktree=delete_worktree, delete_branch=delete_branch)
