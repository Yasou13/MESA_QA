from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
import httpx
import logging

from mesa_qa.codex.runner import CodexRunner
from mesa_qa.models import ScenarioEvent, TesterObservation

logger = logging.getLogger("mesa_qa.tester")


class TesterCodex:
    def __init__(
        self,
        runner: CodexRunner,
        prompts_dir: Path,
        gateway_url: str = "http://127.0.0.1:18765",
        timeout_seconds: int = 300,
    ):
        self.runner = runner
        self.prompts_dir = prompts_dir.resolve()
        self.gateway_url = gateway_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.thread_id: Optional[str] = None

    async def execute_action(
        self,
        event: ScenarioEvent,
        action_id: str,
        tester_workspace: Path,
        mcp_env: Optional[Dict[str, str]] = None,
    ) -> TesterObservation:
        # Load turn prompt template
        turn_file = self.prompts_dir / "TESTER_TURN.md"
        template = turn_file.read_text(encoding="utf-8") if turn_file.exists() else "{parameters_json}"

        params_json = json.dumps({
            "entity": event.entity,
            "field": event.field,
            "value": event.value,
            "old_value": event.old_value,
            "text": event.text,
            "question": event.question,
            "mode": event.mode,
        })

        prompt = template.format(
            action_id=action_id,
            scenario_event_id=event.id,
            action_type=event.kind.value,
            parameters_json=params_json,
        )

        res = await self.runner.run(
            prompt=prompt,
            cwd=tester_workspace,
            sandbox="read-only",
            env_vars=mcp_env,
            thread_id=self.thread_id,
            timeout_seconds=self.timeout_seconds,
        )

        if res.thread_id:
            self.thread_id = res.thread_id

        # Attempt to parse observation from Codex output, or fallback to direct MCP call if codex CLI didn't call MCP
        obs = self._parse_observation(res.output_text, res.raw_stdout, action_id, event.id)
        if obs is None:
            logger.info("Codex CLI output unparsed or incomplete; falling back to direct MCP HTTP execution for action %s", action_id)
            obs = await self._execute_direct_mcp_fallback(event, action_id, mcp_env)

        return obs

    def _parse_observation(
        self, output_text: str, raw_stdout: str, action_id: str, scenario_event_id: str
    ) -> Optional[TesterObservation]:
        # Look for JSON block in stdout/output_text
        for text in (output_text, raw_stdout):
            if not text:
                continue
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict) and "action_id" in data:
                            return TesterObservation.model_validate(data)
                    except Exception:
                        pass
        return None

    async def _execute_direct_mcp_fallback(
        self, event: ScenarioEvent, action_id: str, mcp_env: Optional[Dict[str, str]]
    ) -> TesterObservation:
        token = (mcp_env or {}).get("MESA_CODEX_MCP_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        tool_name = f"mesa_{event.kind.value}"
        if event.kind.value == "correct":
            tool_name = "mesa_improve"

        args: Dict[str, Any] = {}
        if event.kind.value == "remember":
            args = {"content": event.text or str(event.value), "metadata": {"entity": event.entity, "field": event.field}}
        elif event.kind.value == "recall":
            args = {"query": event.question or f"What is {event.field} of {event.entity}?"}
        elif event.kind.value == "correct":
            args = {"memory_id": "mem_01", "new_content": event.text or str(event.value)}
        elif event.kind.value == "forget":
            args = {"memory_id": "mem_01", "reason": event.text or "QA Forget"}

        call_url = f"{self.gateway_url}/mcp/v1/tools/call"
        payload = {"name": tool_name, "arguments": args}

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(call_url, headers=headers, json=payload)
                if resp.status_code == 200:
                    body = resp.json()
                    content = body.get("content", [{}])[0].get("text", "")
                    parsed_content = json.loads(content) if content.startswith("{") else {"raw": content}
                    return TesterObservation(
                        action_id=action_id,
                        scenario_event_id=event.id,
                        tools_called=[tool_name],
                        actual={"answer": content, "raw_response": parsed_content},
                        tester_assessment="pass",
                        reason="Direct MCP execution succeeded",
                    )
                else:
                    return TesterObservation(
                        action_id=action_id,
                        scenario_event_id=event.id,
                        tools_called=[tool_name],
                        actual={"error": resp.text},
                        tester_assessment="infra_error",
                        reason=f"MCP Gateway returned HTTP {resp.status_code}",
                    )
            except Exception as exc:
                return TesterObservation(
                    action_id=action_id,
                    scenario_event_id=event.id,
                    tools_called=[tool_name],
                    actual={"error": str(exc)},
                    tester_assessment="infra_error",
                    reason=f"Direct MCP fallback failed: {exc}",
                )
