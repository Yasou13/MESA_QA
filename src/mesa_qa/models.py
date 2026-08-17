from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    REMEMBER = "remember"
    RECALL = "recall"
    CORRECT = "correct"
    FORGET = "forget"
    ROTATE_SESSION = "rotate_session"
    RESTART_RUNTIME = "restart_runtime"
    DUPLICATE = "duplicate"
    SEMANTIC_DUPLICATE = "semantic_duplicate"
    MULTI_FACT = "multi_fact"
    CONFLICT = "conflict"
    IDEMPOTENCY = "idempotency"


class ScenarioEvent(BaseModel):
    id: str
    template_id: Optional[str] = None
    epoch: int = 0
    kind: ActionKind
    entity: str
    field: Optional[str] = None
    value: Optional[Any] = None
    old_value: Optional[Any] = None
    text: Optional[str] = None
    question: Optional[str] = None
    mode: str = "current"  # current, historical, forgotten
    expected: Optional[Any] = None
    idempotency_key: Optional[str] = None
    idempotency_strategy: Optional[str] = None  # "fresh_attempt", "reuse_same_key"
    effective_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TesterObservation(BaseModel):
    __test__ = False
    action_id: str
    scenario_event_id: str
    tools_called: List[str] = Field(default_factory=list)
    actual: Dict[str, Any] = Field(default_factory=dict)
    tester_assessment: str = "pass"  # pass, suspicious, infra_error
    reason: str = ""
    needs_recheck: bool = False


class Verdict(BaseModel):
    is_pass: bool
    is_candidate_anomaly: bool
    category: str = "NORMAL"
    reason: str = ""
    expected: Any = None
    actual: Any = None


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class BugReport(BaseModel):
    bug_id: str
    run_id: str
    first_seen_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: Severity = Severity.P1
    category: str
    scenario_id: str
    reproduction_strategy: str = "fresh_attempt"
    preconditions: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    expected: Dict[str, Any] = Field(default_factory=dict)
    actual: Dict[str, Any] = Field(default_factory=dict)
    repeat_count: int = 1
    candidate_commit_before: str
    storage_snapshot_id: Optional[str] = None


class RepairResult(BaseModel):
    bug_id: str
    success: bool
    pre_fix_test_passed: bool = False
    post_fix_test_passed: bool = False
    commit_sha: Optional[str] = None
    files_changed: List[str] = Field(default_factory=list)
    targeted_tests_run: List[str] = Field(default_factory=list)
    targeted_tests_passed: bool = False
    live_repro_passed: bool = False
    error_message: Optional[str] = None
