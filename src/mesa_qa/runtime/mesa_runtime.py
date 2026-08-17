from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional
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
        runtime_profile: str = "combined",
        model_enabled: bool = True,
        external_provider_enabled: bool = True,
        llm_provider: str = "mock",
        validation_mode: Optional[int] = 0,
        log_file: Optional[Path] = None,
    ):
        self.candidate_worktree = candidate_worktree.resolve()
        self.python_bin = Path(python_bin).absolute()
        self.storage_root = storage_root.resolve()
        self.port = port
        self.api_key = api_key
        self.principal_id = principal_id
        self.runtime_profile = runtime_profile
        self.model_enabled = model_enabled
        self.external_provider_enabled = external_provider_enabled
        self.llm_provider = llm_provider
        self.validation_mode = validation_mode
        self.log_file = log_file
        self._process: Optional[asyncio.subprocess.Process] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            logger.info(
                "MESA Candidate Runtime is already running (PID %d)", self._process.pid
            )
            return

        env = {
            "PATH": f"{self.python_bin.parent}:{os.environ.get('PATH', '')}",
            "VIRTUAL_ENV": str(self.python_bin.parent.parent),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "USER": os.environ.get("USER", "yasin"),
            "MESA_RUNTIME_PROFILE": self.runtime_profile,
            "MESA_STORAGE_ROOT": str(self.storage_root),
            "MESA_LOAD_DOTENV": "false",
            "MESA_MODEL_ENABLED": "true" if self.model_enabled else "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": (
                "true" if self.external_provider_enabled else "false"
            ),
            "MESA_LLM_PROVIDER": self.llm_provider,
            "MESA_EXTRACTION_PROVIDER": self.llm_provider,
            "MESA_EMBEDDING_DIMENSION": "384",
            "MESA_PORT": str(self.port),
            "MESA_API_KEY": self.api_key,
            "MESA_PRINCIPAL_ID": self.principal_id,
            "MESA_PRINCIPAL_TYPE": "SERVICE",
            "MESA_PRINCIPAL_STATUS": "active",
        }

        if self.validation_mode is not None:
            env["MESA_TIER3_MODE"] = str(self.validation_mode)
            if self.validation_mode == 1:
                env["MESA_TIER3_LLM_PROVIDER_A"] = self.llm_provider
                env["MESA_TIER3_LLM_MODEL_A"] = "mesa-qa-validator-a"
            elif self.validation_mode == 2:
                env["MESA_TIER3_LLM_PROVIDER_A"] = self.llm_provider
                env["MESA_TIER3_LLM_MODEL_A"] = "mesa-qa-validator-a"
                env["MESA_TIER3_LLM_PROVIDER_B"] = self.llm_provider
                env["MESA_TIER3_LLM_MODEL_B"] = "mesa-qa-validator-b"

        log_handle = None
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(self.log_file, "a", encoding="utf-8")

        cmd = [str(self.python_bin), "-m", "mesa_memory.runtime_entrypoint"]
        logger.info(
            "Launching MESA candidate runtime: %s (cwd: %s)",
            " ".join(cmd),
            self.candidate_worktree,
        )

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

        logger.info("MESA candidate process started with PID %d", self._process.pid)

    async def wait_until_ready(self, timeout_seconds: float = 45.0) -> bool:
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            if self._process and self._process.returncode is not None:
                log_snippet = ""
                if self.log_file and self.log_file.exists():
                    try:
                        log_snippet = self.log_file.read_text(encoding="utf-8")[-1000:]
                    except Exception:
                        pass
                logger.error(
                    "MESA process exited prematurely with code %d. Log snippet:\n%s",
                    self._process.returncode,
                    log_snippet,
                )
                return False
            health = await check_mesa_health(
                self.base_url, api_key=self.api_key, timeout=2.0
            )
            if health["status"] == "healthy":
                logger.info("MESA candidate runtime is READY at %s", self.base_url)
                return True
            await asyncio.sleep(1.0)
        logger.error("Timed out waiting for MESA candidate runtime to become ready")
        return False

    async def stop(self) -> None:
        if self._process is not None and self._process.returncode is None:
            logger.info("Stopping MESA candidate process PID %d...", self._process.pid)
            try:
                self._process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "MESA process did not terminate gracefully; force killing..."
                    )
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass
            logger.info("MESA candidate process stopped.")
