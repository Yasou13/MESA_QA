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
    from mesa_qa.codex.tester import _EXPECTED_TOOL_BY_KIND

    prompts = Path(__file__).parents[2] / "prompts"
    expected_tool = _EXPECTED_TOOL_BY_KIND.get(kind.value, "mesa_recall")
    result = CodexRunResult(
        returncode=0,
        raw_stdout=(
            f'{{"type":"tool_call","name":"{expected_tool}"}}\n'
            '{"action_id":"act-1","scenario_event_id":"evt-1","actual":{"answer":"ok"}}'
        ),
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
    assert expected_tool in observation.tools_called


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


@pytest.mark.asyncio
async def test_stale_action_id_rejected(tmp_path):
    prompts = Path(__file__).parents[2] / "prompts"
    # Codex returns stale action_id "act-stale" instead of expected "act-current"
    result = CodexRunResult(
        returncode=0,
        raw_stdout='{"action_id":"act-stale","scenario_event_id":"evt-1","actual":{"answer":"ok"}}',
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-current", tmp_path)

    assert obs.tester_assessment == "infra_error"
    assert "action_id mismatch" in obs.reason
    assert "act-current" in obs.reason
    assert "act-stale" in obs.reason


@pytest.mark.asyncio
async def test_wrong_scenario_event_id_rejected(tmp_path):
    prompts = Path(__file__).parents[2] / "prompts"
    # Codex returns wrong scenario_event_id "evt-wrong" instead of expected "evt-1"
    result = CodexRunResult(
        returncode=0,
        raw_stdout='{"action_id":"act-1","scenario_event_id":"evt-wrong","actual":{"answer":"ok"}}',
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-1", tmp_path)

    assert obs.tester_assessment == "infra_error"
    assert "scenario_event_id mismatch" in obs.reason
    assert "evt-1" in obs.reason
    assert "evt-wrong" in obs.reason


@pytest.mark.asyncio
async def test_missing_identity_rejected(tmp_path):
    prompts = Path(__file__).parents[2] / "prompts"
    # Codex returns output missing action_id or scenario_event_id
    result = CodexRunResult(
        returncode=0,
        raw_stdout='{"actual":{"answer":"ok"},"tester_assessment":"pass"}',
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-1", tmp_path)

    assert obs.tester_assessment == "infra_error"


@pytest.mark.asyncio
async def test_self_reported_mcp_tool_without_stream_event_rejected(tmp_path):
    prompts = Path(__file__).parents[2] / "prompts"
    # Tester self-reports tools_called: ["mesa_recall"] in JSON, but no real tool call exists in the Codex stream
    result = CodexRunResult(
        returncode=0,
        raw_stdout=(
            '{"action_id":"act-1","scenario_event_id":"evt-1",'
            '"tools_called":["mesa_recall"],"actual":{"answer":"FastAPI"},'
            '"tester_assessment":"pass"}'
        ),
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-1", tmp_path)

    # Self-report is non-authoritative -> fails closed as infra_error
    assert obs.tester_assessment == "infra_error"
    assert "Expected MCP tool 'mesa_recall' was not independently observed" in obs.reason
    assert obs.tools_called == []


@pytest.mark.asyncio
async def test_independently_observed_mcp_tool_accepted(tmp_path):
    from mesa_qa.codex.schemas import CodexJSONEvent

    prompts = Path(__file__).parents[2] / "prompts"
    # Codex stream has genuine tool_call event for mesa_recall
    event_call = CodexJSONEvent(
        type="item.created",
        item={"type": "tool_call", "name": "mesa_recall"},
    )
    result = CodexRunResult(
        returncode=0,
        events=[event_call],
        raw_stdout='{"action_id":"act-1","scenario_event_id":"evt-1","actual":{"answer":"FastAPI"},"tester_assessment":"pass"}',
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-1", tmp_path)

    assert obs.tester_assessment == "pass"
    assert "mesa_recall" in obs.tools_called


@pytest.mark.asyncio
async def test_mismatched_mcp_tool_invocation_rejected(tmp_path):
    prompts = Path(__file__).parents[2] / "prompts"
    # Event is RECALL (expected mesa_recall), but stream only called mesa_forget
    result = CodexRunResult(
        returncode=0,
        raw_stdout=(
            '{"type":"tool_call","name":"mesa_forget"}\n'
            '{"action_id":"act-1","scenario_event_id":"evt-1","actual":{"answer":"FastAPI"},"tester_assessment":"pass"}'
        ),
    )
    runner = FakeRunner(result)
    tester = TesterCodex(runner=runner, prompts_dir=prompts)
    event = ScenarioEvent(
        id="evt-1", kind=ActionKind.RECALL, entity="project:atlas"
    )

    obs = await tester.execute_action(event, "act-1", tmp_path)

    assert obs.tester_assessment == "infra_error"
    assert "Expected MCP tool 'mesa_recall' was not independently observed" in obs.reason
    assert "mesa_forget" in obs.tools_called


