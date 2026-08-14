from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
import logging

from mesa_qa.codex.jsonl import parse_codex_stream
from mesa_qa.codex.schemas import CodexRunResult

logger = logging.getLogger("mesa_qa.codex_runner")


class CodexRunner:
    def __init__(self, codex_binary: str = "codex"):
        self.codex_binary = codex_binary

    async def run(
        self,
        prompt: str,
        cwd: Path,
        sandbox: str = "read-only",  # read-only, workspace-write
        env_vars: Optional[Dict[str, str]] = None,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 300,
        launcher_prefix: Optional[List[str]] = None,
        max_output_bytes: int = 1_000_000,
    ) -> CodexRunResult:
        cwd = cwd.resolve()

        # Build command: npx -y @openai/codex or codex binary
        cmd = list(launcher_prefix or []) + [self.codex_binary, "exec", "--json"]

        if sandbox == "read-only":
            cmd.extend(["--sandbox", "read-only"])
        elif sandbox == "workspace-write":
            cmd.extend(["--sandbox", "workspace-write"])

        if thread_id:
            cmd.extend(["resume", thread_id])

        cmd.append(prompt)

        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        logger.info("Executing Codex CLI in %s (sandbox: %s)...", cwd, sandbox)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    self._capture_output(process, max_output_bytes), timeout=float(timeout_seconds)
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error("Codex execution timed out after %d seconds", timeout_seconds)
                return CodexRunResult(
                    returncode=124,
                    raw_stderr="Codex execution timed out",
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            events = parse_codex_stream(stdout)
            thread = thread_id
            for ev in events:
                if ev.thread_id:
                    thread = ev.thread_id

            output_text = "\n".join([ev.delta or "" for ev in events if ev.delta]).strip()

            return CodexRunResult(
                returncode=process.returncode or 0,
                thread_id=thread,
                events=events,
                output_text=output_text,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )

        except Exception as exc:
            logger.exception("Codex execution error: %s", exc)
            return CodexRunResult(
                returncode=1,
                raw_stderr=str(exc),
            )

    async def _capture_output(
        self, process: asyncio.subprocess.Process, limit: int
    ) -> tuple[bytes, bytes]:
        async def drain(stream: asyncio.StreamReader) -> bytes:
            captured = bytearray()
            truncated = False
            while chunk := await stream.read(65536):
                remaining = max(0, limit - len(captured))
                captured.extend(chunk[:remaining])
                truncated = truncated or len(chunk) > remaining
            if truncated:
                captured.extend(b"\n[mesa-qa output truncated]\n")
            return bytes(captured)

        stdout_task = asyncio.create_task(drain(process.stdout))
        stderr_task = asyncio.create_task(drain(process.stderr))
        await process.wait()
        return await stdout_task, await stderr_task
