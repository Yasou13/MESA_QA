from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional
import logging

logger = logging.getLogger("mesa_qa.mcp_binding")


class MCPBindingManager:
    def __init__(
        self,
        candidate_worktree: Path,
        python_bin: Path,
        control_db_path: Path,
        gateway_url: str = "http://127.0.0.1:18765",
    ):
        self.candidate_worktree = candidate_worktree.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.control_db_path = control_db_path.resolve()
        self.gateway_url = gateway_url

    def install_binding(self, tester_workspace: Path, client_id: str = "codex-qa-tester") -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        tester_workspace.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)

        cmd = [
            str(self.python_bin),
            "-m",
            "mesa_mcp.codex_cli",
            "codex",
            "install",
            "--workspace",
            str(tester_workspace),
            "--control-db",
            str(self.control_db_path),
            "--gateway-url",
            self.gateway_url,
            "--client-id",
            client_id,
            "--principal-id",
            "local-qa-tester",
        ]

        logger.info("Installing MESA MCP binding for workspace %s...", tester_workspace)
        res = subprocess.run(cmd, cwd=str(tester_workspace), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            logger.error("Failed to install MESA MCP binding:\n%s", res.stderr)
            raise RuntimeError(f"mesa codex install failed: {res.stderr}")

        logger.info("MESA MCP binding installed successfully.")
        return {"status": "installed", "workspace": str(tester_workspace)}

    def run_doctor(self, tester_workspace: Path) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)

        cmd = [
            str(self.python_bin),
            "-m",
            "mesa_mcp.codex_cli",
            "codex",
            "doctor",
            "--workspace",
            str(tester_workspace),
            "--control-db",
            str(self.control_db_path),
            "--gateway-url",
            self.gateway_url,
        ]

        res = subprocess.run(cmd, cwd=str(tester_workspace), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            logger.warning("MESA codex doctor reported issues:\n%s", res.stdout or res.stderr)
            return {"status": "degraded", "issues": res.stderr}

        return {"status": "ok"}
