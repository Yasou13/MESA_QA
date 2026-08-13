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
