from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging

from mesa_qa.config import QAConfig
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.runtime.mesa_runtime import MesaCandidateRuntime
from mesa_qa.runtime.mcp_gateway import MesaMCPGatewayProcess
from mesa_qa.storage.paths import assert_safe_paths

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
        self.mesa_runtime: Optional[MesaCandidateRuntime] = None
        self.mcp_gateway: Optional[MesaMCPGatewayProcess] = None

    def setup_worktree(self, run_id: str, baseline_commit: Optional[str] = None) -> Path:
        self.candidate_worktree, self.candidate_branch = self.worktree_mgr.create_candidate_worktree(
            run_id=run_id, baseline_commit=baseline_commit
        )
        qa_storage = self.run_dir / "mesa-storage"
        assert_safe_paths(
            main_repo=self.config.mesa.repo_path,
            candidate_worktree=self.candidate_worktree,
            qa_storage=qa_storage,
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
            log_file=logs_dir / "mcp_gateway.log",
        )
        await self.mcp_gateway.start()
        if not await self.mcp_gateway.wait_until_ready(timeout_seconds=30.0):
            raise RuntimeError("Failed to start MESA MCP Gateway")

    async def restart_all(self) -> None:
        logger.info("Restarting MESA candidate services...")
        await self.stop_all()
        await self.start_all()
        logger.info("MESA candidate services successfully restarted.")

    async def stop_all(self) -> None:
        if self.mcp_gateway:
            await self.mcp_gateway.stop()
        if self.mesa_runtime:
            await self.mesa_runtime.stop()

    def teardown(self, delete_worktree: bool = True) -> None:
        if delete_worktree and self.candidate_worktree:
            self.worktree_mgr.remove_candidate_worktree(
                self.candidate_worktree, delete_branch=False, branch_name=self.candidate_branch
            )
