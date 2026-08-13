from __future__ import annotations

from typing import Any, Dict
from mesa_qa.models import ActionKind, ScenarioEvent


def make_remember_event(event_id: str, entity: str, field: str, value: Any, text: str) -> ScenarioEvent:
    return ScenarioEvent(
        id=event_id,
        kind=ActionKind.REMEMBER,
        entity=entity,
        field=field,
        value=value,
        text=text,
    )


def make_recall_event(event_id: str, entity: str, field: str, question: str, expected: Any, mode: str = "current") -> ScenarioEvent:
    return ScenarioEvent(
        id=event_id,
        kind=ActionKind.RECALL,
        entity=entity,
        field=field,
        question=question,
        expected=expected,
        mode=mode,
    )


def make_correct_event(event_id: str, entity: str, field: str, old_value: Any, value: Any, text: str) -> ScenarioEvent:
    return ScenarioEvent(
        id=event_id,
        kind=ActionKind.CORRECT,
        entity=entity,
        field=field,
        old_value=old_value,
        value=value,
        text=text,
    )


def make_forget_event(event_id: str, entity: str, field: str, text: str) -> ScenarioEvent:
    return ScenarioEvent(
        id=event_id,
        kind=ActionKind.FORGET,
        entity=entity,
        field=field,
        text=text,
    )
