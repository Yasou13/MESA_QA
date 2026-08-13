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
