from __future__ import annotations

from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Set
import logging

logger = logging.getLogger("mesa_qa.state_machine")


class State(str, Enum):
    INIT = "INIT"
    PREFLIGHT = "PREFLIGHT"
    CREATE_CANDIDATE = "CREATE_CANDIDATE"
    START_MESA = "START_MESA"
    START_MCP = "START_MCP"
    RUNNING = "RUNNING"
    ANOMALY = "ANOMALY"
    RECHECKING = "RECHECKING"
    REPRODUCING = "REPRODUCING"
    CONFIRMED_BUG = "CONFIRMED_BUG"
    REPAIRING = "REPAIRING"
    VERIFYING = "VERIFYING"
    RESTARTING = "RESTARTING"
    LIVE_RECHECK = "LIVE_RECHECK"
    WAITING_FOR_CODEX = "WAITING_FOR_CODEX"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


VALID_TRANSITIONS: Dict[State, Set[State]] = {
    State.INIT: {State.PREFLIGHT, State.FAILED, State.STOPPING},
    State.PREFLIGHT: {State.CREATE_CANDIDATE, State.FAILED, State.STOPPING},
    State.CREATE_CANDIDATE: {State.START_MESA, State.FAILED, State.STOPPING},
    State.START_MESA: {State.START_MCP, State.FAILED, State.STOPPING},
    State.START_MCP: {State.RUNNING, State.FAILED, State.STOPPING},
    State.RUNNING: {
        State.ANOMALY,
        State.RESTARTING,
        State.PAUSED,
        State.WAITING_FOR_CODEX,
        State.STOPPING,
        State.COMPLETED,
        State.FAILED,
    },
    State.ANOMALY: {State.RECHECKING, State.RUNNING, State.STOPPING, State.FAILED},
    State.RECHECKING: {State.REPRODUCING, State.RUNNING, State.STOPPING, State.FAILED},
    State.REPRODUCING: {State.CONFIRMED_BUG, State.RUNNING, State.STOPPING, State.FAILED},
    State.CONFIRMED_BUG: {State.REPAIRING, State.PAUSED, State.STOPPING, State.FAILED},
    State.REPAIRING: {State.VERIFYING, State.RUNNING, State.STOPPING, State.FAILED},
    State.VERIFYING: {State.RESTARTING, State.RUNNING, State.STOPPING, State.FAILED},
    State.RESTARTING: {State.LIVE_RECHECK, State.RUNNING, State.FAILED, State.STOPPING},
    State.LIVE_RECHECK: {State.RUNNING, State.CONFIRMED_BUG, State.STOPPING, State.FAILED},
    State.WAITING_FOR_CODEX: {State.RUNNING, State.PAUSED, State.STOPPING, State.FAILED},
    State.PAUSED: {State.RUNNING, State.STOPPING, State.FAILED},
    State.STOPPING: {State.COMPLETED, State.FAILED},
    State.COMPLETED: set(),
    State.FAILED: set(),
}


class StateMachine:
    def __init__(self, initial_state: State = State.INIT, on_change: Optional[Callable[[State, State], None]] = None):
        self._current_state = initial_state
        self._on_change = on_change

    @property
    def current(self) -> State:
        return self._current_state

    def transition_to(self, new_state: State) -> None:
        if new_state == self._current_state:
            return
        allowed = VALID_TRANSITIONS.get(self._current_state, set())
        if new_state not in allowed:
            err = f"Invalid state transition: {self._current_state} -> {new_state}"
            logger.error(err)
            raise ValueError(err)
        
        old_state = self._current_state
        self._current_state = new_state
        logger.info("State transition: %s -> %s", old_state, new_state)
        if self._on_change:
            self._on_change(old_state, new_state)
