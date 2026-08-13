from __future__ import annotations

import json
from typing import List, Optional
import logging

from mesa_qa.codex.schemas import CodexJSONEvent

logger = logging.getLogger("mesa_qa.codex_jsonl")


def parse_codex_jsonl_line(line: str) -> Optional[CodexJSONEvent]:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
        if isinstance(data, dict) and "type" in data:
            return CodexJSONEvent.model_validate(data)
    except Exception as exc:
        logger.debug("Failed to parse JSONL line: %s (%s)", line[:100], exc)
    return None


def parse_codex_stream(stdout: str) -> List[CodexJSONEvent]:
    events: List[CodexJSONEvent] = []
    for line in stdout.splitlines():
        event = parse_codex_jsonl_line(line)
        if event:
            events.append(event)
    return events
