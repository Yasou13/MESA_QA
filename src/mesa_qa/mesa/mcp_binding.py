from __future__ import annotations

import os
import subprocess
import hashlib
import shlex
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
        principal_id: str = "local-qa-tester",
        tenant_id: str = "default",
        workspace_id: str = "default",
        dataset_id: str = "default",
    ):
        self.candidate_worktree = candidate_worktree.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.control_db_path = control_db_path.resolve()
        self.gateway_url = gateway_url
        self.principal_id = principal_id
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.dataset_id = dataset_id
        self.mesa_cli = self.python_bin.parent / "mesa"

    def _command(
        self, operation: str, tester_workspace: Path, *, client_id: Optional[str] = None
    ) -> list[str]:
        if not self.mesa_cli.is_file():
            raise RuntimeError(f"MESA console entrypoint is missing: {self.mesa_cli}")
        cmd = [
            str(self.mesa_cli),
            "codex",
            operation,
            "--workspace",
            str(tester_workspace),
            "--control-db",
            str(self.control_db_path),
            "--gateway-url",
            self.gateway_url,
        ]
        if client_id:
            cmd.extend(
                [
                    "--client-id",
                    client_id,
                    "--principal-id",
                    self.principal_id,
                    "--tenant-id",
                    self.tenant_id,
                    "--workspace-id",
                    self.workspace_id,
                    "--dataset-id",
                    self.dataset_id,
                ]
            )
        return cmd

    @staticmethod
    def _json_output(
        res: subprocess.CompletedProcess[str], label: str
    ) -> Dict[str, str]:
        if res.returncode != 0:
            raise RuntimeError(f"mesa codex {label} failed: {res.stderr or res.stdout}")
        try:
            payload = json.loads(res.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"mesa codex {label} returned invalid JSON") from exc
        if payload.get("status") not in {"installed", "ok"}:
            raise RuntimeError(
                f"mesa codex {label} reported unhealthy status: {payload}"
            )
        return payload

    def install_binding(
        self, tester_workspace: Path, client_id: str = "codex-qa-tester"
    ) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        tester_workspace.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)
        env["VIRTUAL_ENV"] = str(self.python_bin.parent.parent)
        env["PATH"] = f"{self.python_bin.parent}:{env.get('PATH', '')}"

        cmd = self._command("install", tester_workspace, client_id=client_id)

        logger.info("Installing MESA MCP binding for workspace %s...", tester_workspace)
        res = subprocess.run(
            cmd,
            cwd=str(tester_workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        payload = self._json_output(res, "install")
        if (
            not (tester_workspace / ".codex" / "config.toml").is_file()
            or not (tester_workspace / ".codex" / "hooks.json").is_file()
        ):
            raise RuntimeError(
                "mesa codex install did not create required workspace binding files"
            )
        payload["workspace"] = str(tester_workspace)
        return payload

    def binding_context(
        self, tester_workspace: Path, binding: Dict[str, str]
    ) -> Dict[str, str]:
        return {
            "client_id": str(binding["client_id"]),
            "binding_id": str(binding["binding_id"]),
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "dataset_id": self.dataset_id,
            "gateway_credential": self._read_gateway_credential(tester_workspace),
        }

    def _read_gateway_credential(self, tester_workspace: Path) -> str:
        identity = str(tester_workspace.resolve())
        remote = subprocess.run(
            [
                "git",
                "-C",
                str(tester_workspace),
                "config",
                "--get",
                "remote.origin.url",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout.strip()
        if remote:
            identity = remote
        fingerprint = f"sha256:{hashlib.sha256(identity.encode()).hexdigest()}"
        config_home = Path(
            os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        )
        env_path = config_home / "mesa" / "codex" / f"{fingerprint}.env"
        if not env_path.is_file():
            raise RuntimeError("MESA Codex credential file is missing after install")
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key == "MESA_CODEX_MCP_TOKEN":
                parsed = shlex.split(value)
                if parsed and parsed[0]:
                    return parsed[0]
        raise RuntimeError(
            "MESA Codex credential file does not contain a gateway token"
        )

    def run_doctor(self, tester_workspace: Path) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)

        cmd = self._command("doctor", tester_workspace)

        res = subprocess.run(
            cmd,
            cwd=str(tester_workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return self._json_output(res, "doctor")

    def run_status(self, tester_workspace: Path) -> Dict[str, str]:
        tester_workspace = tester_workspace.resolve()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)
        res = subprocess.run(
            self._command("status", tester_workspace),
            cwd=str(tester_workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
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
