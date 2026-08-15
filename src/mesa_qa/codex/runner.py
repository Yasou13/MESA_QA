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
        mcp_gateway_url: Optional[str] = None,
        max_output_bytes: int = 1_000_000,
    ) -> CodexRunResult:
        cwd = cwd.resolve()

        # Build command: npx -y @openai/codex or codex binary
        cmd = list(launcher_prefix or []) + [self.codex_binary, "exec", "--json"]
        # The Tester workspace is a QA-owned contained directory rather than a
        # source checkout. Codex otherwise refuses to run before MCP is reached.
        cmd.append("--skip-git-repo-check")
        if mcp_gateway_url:
            server_url = mcp_gateway_url.rstrip("/") + "/mcp"
            cmd.extend(
                [
                    "-c",
                    f'mcp_servers.mesa.url="{server_url}"',
                    "-c",
                    'mcp_servers.mesa.bearer_token_env_var="MESA_CODEX_MCP_TOKEN"',
                    "-c",
                    "mcp_servers.mesa.enabled=true",
                    "-c",
                    "mcp_servers.mesa.required=true",
                    "-c",
                    # Codex may invoke the remote tool noninteractively; MESA's
                    # own durable operator state machine remains authoritative.
                    'mcp_servers.mesa.default_tools_approval_mode="approve"',
                    "-c",
                    (
                        'mcp_servers.mesa.enabled_tools=["mesa_health","mesa_recall",'
                        '"mesa_remember","mesa_improve","mesa_forget",'
                        '"mesa_get_operation_status"]'
                    ),
                ]
            )

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
                    self._capture_output(process, max_output_bytes),
                    timeout=float(timeout_seconds),
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error(
                    "Codex execution timed out after %d seconds", timeout_seconds
                )
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

            output_parts: List[str] = []
            for event in events:
                if event.delta:
                    output_parts.append(event.delta)
                item = event.item or {}
                if item.get("type") == "agent_message" and isinstance(
                    item.get("text"), str
                ):
                    output_parts.append(item["text"])
            output_text = "\n".join(output_parts).strip()

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
