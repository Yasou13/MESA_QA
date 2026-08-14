import pytest
from pathlib import Path
from mesa_qa.scenario.engine import ScenarioEngine

def test_scenario_engine_loading():
    scenarios_dir = Path(__file__).parent.parent.parent / "scenarios"
    engine = ScenarioEngine(scenarios_dir)
    count = engine.load_suite(["basic_memory"])
    assert count > 0
    assert engine.has_next()
    ev = engine.next_event()
    assert ev is not None
    assert ev.id == "evt_bm_01"


def test_scenario_seed_is_reproducible_and_reset_is_real():
    scenarios_dir = Path(__file__).parent.parent.parent / "scenarios"
    first = ScenarioEngine(scenarios_dir, seed=11)
    second = ScenarioEngine(scenarios_dir, seed=11)
    third = ScenarioEngine(scenarios_dir, seed=12)
    first.load_suite(["basic_memory"])
    second.load_suite(["basic_memory"])
    third.load_suite(["basic_memory"])
    first_events = [first.next_event().model_dump() for _ in range(len(first.events))]
    second_events = [second.next_event().model_dump() for _ in range(len(second.events))]
    third_events = [third.next_event().model_dump() for _ in range(len(third.events))]
    assert first_events == second_events
    assert first_events != third_events
    first.reset()
    assert first.next_event().id == "evt_bm_01"
