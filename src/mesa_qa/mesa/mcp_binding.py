from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional
import logging
import json

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
        self.mesa_cli = self.python_bin.parent / "mesa"

    def _command(self, operation: str, tester_workspace: Path, *, client_id: Optional[str] = None) -> list[str]:
        if not self.mesa_cli.is_file():
            raise RuntimeError(f"MESA console entrypoint is missing: {self.mesa_cli}")
        cmd = [str(self.mesa_cli), "codex", operation, "--workspace", str(tester_workspace), "--control-db", str(self.control_db_path), "--gateway-url", self.gateway_url]
        if client_id:
            cmd.extend(["--client-id", client_id, "--principal-id", "local-qa-tester"])
        return cmd

    @staticmethod
    def _json_output(res: subprocess.CompletedProcess[str], label: str) -> Dict[str, str]:
        if res.returncode != 0:
            raise RuntimeError(f"mesa codex {label} failed: {res.stderr or res.stdout}")
        try:
            payload = json.loads(res.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"mesa codex {label} returned invalid JSON") from exc
        if payload.get("status") not in {"installed", "ok"}:
            raise RuntimeError(f"mesa codex {label} reported unhealthy status: {payload}")
        return payload

    def install_binding(self, tester_workspace: Path, client_id: str = "codex-qa-tester") -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        tester_workspace.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)

        cmd = self._command("install", tester_workspace, client_id=client_id)

        logger.info("Installing MESA MCP binding for workspace %s...", tester_workspace)
        res = subprocess.run(cmd, cwd=str(tester_workspace), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        payload = self._json_output(res, "install")
        if not (tester_workspace / ".codex" / "config.toml").is_file() or not (tester_workspace / ".codex" / "hooks.json").is_file():
            raise RuntimeError("mesa codex install did not create required workspace binding files")
        payload["workspace"] = str(tester_workspace)
        return payload

    def run_doctor(self, tester_workspace: Path) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)

        cmd = self._command("doctor", tester_workspace)

        res = subprocess.run(cmd, cwd=str(tester_workspace), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return self._json_output(res, "doctor")

    def run_status(self, tester_workspace: Path) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)
        res = subprocess.run(
            self._command("status", tester_workspace), cwd=str(tester_workspace), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"mesa codex status failed: {res.stderr or res.stdout}")
        try:
            payload = json.loads(res.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("mesa codex status returned invalid JSON") from exc
        credential = payload.get("credential") or {}
        if not payload.get("configured") or credential.get("status") != "ACTIVE":
            raise RuntimeError(f"MESA Codex binding is not active: {payload}")
        return payload

    def launcher_prefix(self, tester_workspace: Path) -> list[str]:
        return self._command("run", tester_workspace)
