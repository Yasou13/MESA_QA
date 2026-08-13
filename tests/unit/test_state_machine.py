import pytest
from mesa_qa.state_machine import State, StateMachine

def test_valid_state_transitions():
    sm = StateMachine(initial_state=State.INIT)
    sm.transition_to(State.PREFLIGHT)
    sm.transition_to(State.CREATE_CANDIDATE)
    sm.transition_to(State.START_MESA)
    sm.transition_to(State.START_MCP)
    sm.transition_to(State.RUNNING)
    assert sm.current == State.RUNNING

def test_invalid_state_transition_raises():
    sm = StateMachine(initial_state=State.INIT)
    with pytest.raises(ValueError, match="Invalid state transition"):
        sm.transition_to(State.RUNNING)
