from __future__ import annotations

from pathlib import Path
from typing import Dict, Any
import logging

from mesa_qa.mesa.mcp_binding import MCPBindingManager

logger = logging.getLogger("mesa_qa.bootstrap")


class MESABootstrap:
    def __init__(self, candidate_worktree: Path, python_bin: Path, control_db_path: Path, gateway_url: str):
        self.binding_mgr = MCPBindingManager(
            candidate_worktree=candidate_worktree,
            python_bin=python_bin,
            control_db_path=control_db_path,
            gateway_url=gateway_url,
        )

    def prepare_tester_workspace(self, tester_dir: Path) -> Dict[str, Any]:
        logger.info("Bootstrapping tester workspace at %s", tester_dir)
        binding_res = self.binding_mgr.install_binding(tester_workspace=tester_dir)
        doctor_res = self.binding_mgr.run_doctor(tester_workspace=tester_dir)
        return {
            "binding": binding_res,
            "doctor": doctor_res,
        }
