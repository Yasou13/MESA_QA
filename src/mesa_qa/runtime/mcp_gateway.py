from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path
import sys
import logging

from mesa_qa.runtime.health import check_mcp_gateway_health

logger = logging.getLogger("mesa_qa.mcp_gateway")


class MesaMCPGatewayProcess:
    def __init__(
        self,
        candidate_worktree: Path,
        python_bin: Path,
        control_db_path: Path,
        gateway_port: int = 18765,
        mesa_api_url: str = "http://127.0.0.1:18000",
        mesa_api_key: str = "qa-secret-key-12345",
        encryption_key: str = "zmUcsEJCGvJV38DIi6g3WTIrDoD3hLer6xAUxwGUtyg=",
        log_file: Optional[Path] = None,
    ):
        self.candidate_worktree = candidate_worktree.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.control_db_path = control_db_path.resolve()
        self.gateway_port = gateway_port
        self.mesa_api_url = mesa_api_url
        self.mesa_api_key = mesa_api_key
        self.encryption_key = encryption_key
        self.log_file = log_file
        self._process: Optional[asyncio.subprocess.Process] = None

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway_port}"

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            logger.info("MESA MCP Gateway is already running (PID %d)", self._process.pid)
            return

        self.control_db_path.parent.mkdir(parents=True, exist_ok=True)

        env = {
            "PATH": f"{self.python_bin.parent}:{os.environ.get('PATH', '')}",
            "VIRTUAL_ENV": str(self.python_bin.parent.parent),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "USER": os.environ.get("USER", "yasin"),
            "MESA_GATEWAY_CONTROL_DB": str(self.control_db_path),
            "MESA_GATEWAY_ENCRYPTION_KEY": self.encryption_key,
            "MESA_BASE_URL": self.mesa_api_url,
            "MESA_API_KEY": self.mesa_api_key,
            "MESA_USE_V4": "true",
        }

        log_handle = None
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(self.log_file, "a", encoding="utf-8")

        # Command to launch uvicorn with gateway app
        cmd = [
            str(self.python_bin),
            "-m",
            "uvicorn",
            "mesa_mcp.gateway.app:create_gateway_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.gateway_port),
        ]
        logger.info("Launching MESA MCP Gateway: %s", " ".join(cmd))

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.candidate_worktree),
                env=env,
                stdout=log_handle if log_handle is not None else subprocess.DEVNULL,
                stderr=log_handle if log_handle is not None else subprocess.DEVNULL,
            )
        finally:
            if log_handle is not None:
                log_handle.close()

        logger.info("MESA MCP Gateway started with PID %d", self._process.pid)

    async def wait_until_ready(self, timeout_seconds: float = 30.0) -> bool:
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            if self._process and self._process.returncode is not None:
                logger.error("MESA Gateway process exited prematurely with code %d", self._process.returncode)
                return False
            health = await check_mcp_gateway_health(self.gateway_url, timeout=2.0)
            if health["status"] in ("healthy", "auth_required"):
                logger.info("MESA MCP Gateway is READY at %s", self.gateway_url)
                return True
            await asyncio.sleep(1.0)
        logger.error("Timed out waiting for MESA MCP Gateway to become ready")
        return False

    async def stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        logger.info("Stopping MESA MCP Gateway process PID %d...", self._process.pid)
        try:
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass
        logger.info("MESA MCP Gateway process stopped.")
