from mesa_qa.codex.jsonl import parse_codex_stream


def test_parse_codex_stream():
    sample_stdout = """
{"type": "thread.started", "thread_id": "thread_abc123"}
{"type": "item.created", "delta": "Hello world"}
{"type": "turn.completed"}
"""
    events = parse_codex_stream(sample_stdout)
    assert len(events) == 3
    assert events[0].thread_id == "thread_abc123"
    assert events[1].delta == "Hello world"


def test_current_codex_agent_message_event_is_retained():
    stdout = (
        '{"type":"thread.started","thread_id":"thread-new"}\n'
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"{\\"probe\\":\\"ok\\"}"}}\n'
    )
    events = parse_codex_stream(stdout)
    parts = []
    for event in events:
        item = event.item or {}
        if item.get("type") == "agent_message":
            parts.append(item["text"])
    assert parts == ['{"probe":"ok"}']
