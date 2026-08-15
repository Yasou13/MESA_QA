from pathlib import Path

import pytest

from mesa_qa.codex.schemas import CodexRunResult
from mesa_qa.codex.tester import TesterCodex
from mesa_qa.models import ActionKind, ScenarioEvent


class FakeRunner:
    def __init__(self, result: CodexRunResult):
        self.result = result
        self.prompt = ""
        self.kwargs = {}

    async def run(self, prompt, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ActionKind))
async def test_tester_prompt_is_rendered_without_formatting_json_braces(tmp_path, kind):
    prompts = Path(__file__).parents[2] / "prompts"
    result = CodexRunResult(
        returncode=0,
        raw_stdout='{"action_id":"act-1","scenario_event_id":"evt-1","actual":{"answer":"ok"}}',
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=kind, entity="project:atlas", field="backend", value="FastAPI"
    )

    observation = await tester.execute_action(event, "act-1", tmp_path)

    assert observation.tester_assessment == "pass"
    assert '"action_id": "act-1"' in runner.prompt  # literal JSON contract survives
    assert "You are a QA test engineer" in runner.prompt
    assert runner.kwargs["mcp_gateway_url"] == "http://127.0.0.1:18765"


@pytest.mark.asyncio
async def test_tester_codex_failure_is_not_replaced_by_direct_mcp(tmp_path):
    runner = FakeRunner(CodexRunResult(returncode=1, raw_stderr="codex unavailable"))
    tester = TesterCodex(
        runner=runner, prompts_dir=Path(__file__).parents[2] / "prompts"
    )
    event = ScenarioEvent(id="evt-1", kind=ActionKind.RECALL, entity="project:atlas")

    observation = await tester.execute_action(event, "act-1", tmp_path)

    assert observation.tester_assessment == "infra_error"
    assert "Codex failed" in observation.reason


def test_thread_rotation_forgets_codex_conversation_id(tmp_path):
    tester = TesterCodex(
        runner=FakeRunner(CodexRunResult(returncode=0)),
        prompts_dir=Path(__file__).parents[2] / "prompts",
    )
    tester.thread_id = "old-thread"
    tester.rotate_thread()
    assert tester.thread_id is None


def test_multiline_fenced_observation_is_parsed(tmp_path):
    tester = TesterCodex(
        runner=FakeRunner(CodexRunResult(returncode=0)),
        prompts_dir=Path(__file__).parents[2] / "prompts",
    )
    output = """Result:\n```json\n{
      "action_id": "act-1",
      "scenario_event_id": "evt-1",
      "tools_called": ["mesa_remember"],
      "actual": {"operation_id": "op_123"},
      "tester_assessment": "pass",
      "reason": "pending approval",
      "needs_recheck": false
    }\n```"""
    parsed = tester._parse_observation(output, "", "act-1", "evt-1")
    assert parsed is not None
    assert parsed.actual["operation_id"] == "op_123"
