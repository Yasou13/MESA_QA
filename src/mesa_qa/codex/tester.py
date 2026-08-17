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
        model: Optional[str] = None,
        json_events: bool = True,
    ):
        self.runner = runner
        self.prompts_dir = prompts_dir.resolve()
        self.gateway_url = gateway_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.json_events = json_events
        self.thread_id: Optional[str] = None
        self._launcher_prefix: Optional[List[str]] = None

    def configure_mesa_launcher(
        self, launcher_prefix: List[str], *, gateway_url: Optional[str] = None
    ) -> None:
        """Require MESA's protected `mesa codex run` launcher for tester turns."""
        self._launcher_prefix = list(launcher_prefix)
        if gateway_url:
            self.gateway_url = gateway_url.rstrip("/")

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
        template = (
            turn_file.read_text(encoding="utf-8")
            if turn_file.exists()
            else "{parameters_json}"
        )
        system_file = self.prompts_dir / "TESTER_SYSTEM.md"
        system_contract = (
            system_file.read_text(encoding="utf-8") if system_file.exists() else ""
        )

        params_json = json.dumps(
            {
                "entity": event.entity,
                "field": event.field,
                "value": event.value,
                "old_value": event.old_value,
                "text": event.text,
                "question": event.question,
                "mode": event.mode,
                "idempotency_key": event.idempotency_key,
            }
        )

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
            mcp_gateway_url=self.gateway_url,
            model=self.model,
            json_events=self.json_events,
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

        obs = self._parse_observation(
            res.output_text, res.raw_stdout, action_id, event.id
        )
        if obs is None:
            output_tail = res.output_text.strip()[-1000:]
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tester_assessment="infra_error",
                reason=(
                    "Codex completed without a valid structured Tester observation"
                    + (f": {output_tail}" if output_tail else "")
                ),
            )

        # Strict identity verification: action_id and scenario_event_id must exactly match
        if not obs.action_id or obs.action_id != action_id:
            logger.warning(
                "Tester returned invalid or mismatched action_id: expected '%s', got '%s'",
                action_id,
                obs.action_id,
            )
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tester_assessment="infra_error",
                reason=f"Tester action_id mismatch or missing: expected '{action_id}', got '{obs.action_id}'",
            )

        if not obs.scenario_event_id or obs.scenario_event_id != event.id:
            logger.warning(
                "Tester returned invalid or mismatched scenario_event_id: expected '%s', got '%s'",
                event.id,
                obs.scenario_event_id,
            )
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tester_assessment="infra_error",
                reason=f"Tester scenario_event_id mismatch or missing: expected '{event.id}', got '{obs.scenario_event_id}'",
            )

        # Independently prove real MCP tool invocation from Codex stream events
        observed_tools = self._extract_observed_tools(res)
        obs.tools_called = observed_tools

        expected_tool = _EXPECTED_TOOL_BY_KIND.get(event.kind.value)
        if expected_tool and expected_tool not in observed_tools:
            logger.warning(
                "Action '%s' expected MCP tool '%s' but observed tools were %s",
                action_id,
                expected_tool,
                observed_tools,
            )
            return TesterObservation(
                action_id=action_id,
                scenario_event_id=event.id,
                tools_called=observed_tools,
                tester_assessment="infra_error",
                reason=(
                    f"Expected MCP tool '{expected_tool}' was not independently observed "
                    f"in Codex stream (observed: {observed_tools})"
                ),
            )

        return obs

    def _extract_observed_tools(self, res: CodexRunResult) -> List[str]:
        observed: List[str] = []

        for ev in res.events:
            if isinstance(ev.tool_call, dict):
                name = (
                    ev.tool_call.get("name")
                    or ev.tool_call.get("tool")
                    or ev.tool_call.get("tool_name")
                    or (
                        ev.tool_call.get("function")
                        if isinstance(ev.tool_call.get("function"), dict)
                        else {}
                    ).get("name")
                )
                if name and isinstance(name, str) and name not in observed:
                    observed.append(name)

            if isinstance(ev.item, dict):
                item_type = ev.item.get("type", "")
                if (
                    item_type
                    in (
                        "tool_call",
                        "mcp_tool_call",
                        "function_call",
                        "tool_use",
                        "mcp_call",
                        "call",
                    )
                    or "tool" in item_type
                ):
                    name = (
                        ev.item.get("name")
                        or ev.item.get("tool")
                        or ev.item.get("tool_name")
                        or (
                            ev.item.get("function")
                            if isinstance(ev.item.get("function"), dict)
                            else {}
                        ).get("name")
                    )
                    if name and isinstance(name, str) and name not in observed:
                        observed.append(name)
                elif (
                    ev.item.get("name")
                    and isinstance(ev.item.get("name"), str)
                    and ev.item.get("name").startswith("mesa_")
                ):
                    if ev.item["name"] not in observed:
                        observed.append(ev.item["name"])

        if res.raw_stdout:
            for line in res.raw_stdout.splitlines():
                line = line.strip()
                if not line.startswith("{") or not line.endswith("}"):
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    for key in ("tool_call", "mcp_tool_call", "function_call"):
                        if isinstance(data.get(key), dict):
                            tc = data[key]
                            name = (
                                tc.get("name")
                                or tc.get("tool")
                                or (
                                    tc.get("function")
                                    if isinstance(tc.get("function"), dict)
                                    else {}
                                ).get("name")
                            )
                            if name and isinstance(name, str) and name not in observed:
                                observed.append(name)
                    if isinstance(data.get("item"), dict):
                        item = data["item"]
                        name = (
                            item.get("name")
                            or item.get("tool")
                            or (
                                item.get("function")
                                if isinstance(item.get("function"), dict)
                                else {}
                            ).get("name")
                        )
                        if (
                            name
                            and isinstance(name, str)
                            and (
                                name.startswith("mesa_")
                                or item.get("type")
                                in (
                                    "tool_call",
                                    "mcp_tool_call",
                                    "function_call",
                                )
                            )
                            and name not in observed
                        ):
                            observed.append(name)
                    if isinstance(data.get("tool_calls"), list):
                        for tc in data["tool_calls"]:
                            if isinstance(tc, dict):
                                name = (
                                    tc.get("name")
                                    or tc.get("tool")
                                    or (
                                        tc.get("function")
                                        if isinstance(tc.get("function"), dict)
                                        else {}
                                    ).get("name")
                                )
                                if (
                                    name
                                    and isinstance(name, str)
                                    and name not in observed
                                ):
                                    observed.append(name)
                    if data.get("type") == "tool_call" and isinstance(
                        data.get("name"), str
                    ):
                        if data["name"] not in observed:
                            observed.append(data["name"])
                except Exception:
                    continue

        return observed

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
            decoder = json.JSONDecoder()
            for index, character in enumerate(text):
                if character != "{":
                    continue
                try:
                    data, _end = decoder.raw_decode(text[index:])
                    if isinstance(data, dict) and "action_id" in data:
                        return TesterObservation.model_validate(data)
                except Exception:
                    continue
        return None


_EXPECTED_TOOL_BY_KIND = {
    "remember": "mesa_remember",
    "recall": "mesa_recall",
    "correct": "mesa_improve",
    "forget": "mesa_forget",
    "duplicate": "mesa_remember",
    "semantic_duplicate": "mesa_remember",
    "multi_fact": "mesa_remember",
    "conflict": "mesa_improve",
    "idempotency": "mesa_remember",
}
