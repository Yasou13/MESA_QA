from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path
import sys
import logging

from mesa_qa.runtime.health import check_mesa_health

logger = logging.getLogger("mesa_qa.mesa_runtime")


class MesaCandidateRuntime:
    def __init__(
        self,
        candidate_worktree: Path,
        python_bin: Path,
        storage_root: Path,
        port: int = 18000,
        api_key: str = "qa-secret-key-12345",
        principal_id: str = "qa-service-principal",
        log_file: Optional[Path] = None,
    ):
        self.candidate_worktree = candidate_worktree.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.storage_root = storage_root.resolve()
        self.port = port
        self.api_key = api_key
        self.principal_id = principal_id
        self.log_file = log_file
        self._process: Optional[asyncio.subprocess.Process] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            logger.info("MESA Candidate Runtime is already running (PID %d)", self._process.pid)
            return

        env = {
            "PATH": f"{self.python_bin.parent}:{os.environ.get('PATH', '')}",
            "VIRTUAL_ENV": str(self.python_bin.parent.parent),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "USER": os.environ.get("USER", "yasin"),
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(self.storage_root),
            "MESA_PORT": str(self.port),
            "MESA_API_KEY": self.api_key,
            "MESA_PRINCIPAL_ID": self.principal_id,
            "MESA_PRINCIPAL_TYPE": "SERVICE",
            "MESA_PRINCIPAL_STATUS": "active",
        }

        log_out = open(self.log_file, "a") if self.log_file else subprocess.DEVNULL

        cmd = [str(self.python_bin), "-m", "mesa_memory.runtime_entrypoint"]
        logger.info("Launching MESA candidate runtime: %s (cwd: %s)", " ".join(cmd), self.candidate_worktree)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.candidate_worktree),
            env=env,
            stdout=log_out,
            stderr=log_out,
        )
        logger.info("MESA candidate process started with PID %d", self._process.pid)

    async def wait_until_ready(self, timeout_seconds: float = 45.0) -> bool:
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            if self._process and self._process.returncode is not None:
                logger.error("MESA process exited prematurely with code %d", self._process.returncode)
                return False
            health = await check_mesa_health(self.base_url, api_key=self.api_key, timeout=2.0)
            if health["status"] == "healthy":
                logger.info("MESA candidate runtime is READY at %s", self.base_url)
                return True
            await asyncio.sleep(1.0)
        logger.error("Timed out waiting for MESA candidate runtime to become ready")
        return False

    async def stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        logger.info("Stopping MESA candidate process PID %d...", self._process.pid)
        try:
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("MESA process did not terminate gracefully; force killing...")
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass
        logger.info("MESA candidate process stopped.")
