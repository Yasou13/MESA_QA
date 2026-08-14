from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
import logging

from mesa_qa.codex.runner import CodexRunner
from mesa_qa.models import ScenarioEvent, TesterObservation

logger = logging.getLogger("mesa_qa.tester")


class TesterCodex:
    __test__ = False
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
        self._launcher_prefix: Optional[List[str]] = None

    def configure_mesa_launcher(self, launcher_prefix: List[str]) -> None:
        """Require MESA's protected `mesa codex run` launcher for tester turns."""
        self._launcher_prefix = list(launcher_prefix)

    def rotate_thread(self) -> None:
        """Drop Codex conversation context; MESA data remains available through MCP."""
        self.thread_id = None

    async def execute_action(
        self,
        event: ScenarioEvent,
        action_id: str,
        tester_workspace: Path,
        mcp_env: Optional[Dict[str, str]] = None,
    ) -> TesterObservation:
        # Use explicit placeholder replacement: the template intentionally contains JSON braces.
        turn_file = self.prompts_dir / "TESTER_TURN.md"
        template = turn_file.read_text(encoding="utf-8") if turn_file.exists() else "{parameters_json}"
        system_file = self.prompts_dir / "TESTER_SYSTEM.md"
        system_contract = system_file.read_text(encoding="utf-8") if system_file.exists() else ""

        params_json = json.dumps({
            "entity": event.entity,
            "field": event.field,
            "value": event.value,
            "old_value": event.old_value,
            "text": event.text,
            "question": event.question,
            "mode": event.mode,
            "idempotency_key": event.idempotency_key,
        })

        prompt = system_contract + "\n\n" + template
        for placeholder, value in {
            "{action_id}": action_id,
            "{scenario_event_id}": event.id,
            "{action_type}": event.kind.value,
            "{parameters_json}": params_json,
        }.items():
            prompt = prompt.replace(placeholder, value)

        res = await self.runner.run(
            prompt=prompt,
            cwd=tester_workspace,
            sandbox="read-only",
            env_vars=mcp_env,
            thread_id=self.thread_id,
            timeout_seconds=self.timeout_seconds,
            launcher_prefix=self._launcher_prefix,
        )

        if res.thread_id:
            self.thread_id = res.thread_id

        if res.returncode != 0:
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tester_assessment="infra_error",
                reason=f"Codex failed with exit code {res.returncode}: {res.raw_stderr[-500:]}",
            )

        obs = self._parse_observation(res.output_text, res.raw_stdout, action_id, event.id)
        if obs is None:
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tester_assessment="infra_error",
                reason="Codex completed without a valid structured Tester observation",
            )
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
