import pytest
from mesa_qa.config import QAConfig

def test_default_config_loading():
    cfg = QAConfig.load()
    assert cfg.mesa.port == 18000
    assert cfg.mesa.gateway_port == 18765
    assert cfg.safety.max_auto_changed_files == 8
    assert ".github/" in cfg.safety.forbidden_repair_paths

def test_lite_profile_loading():
    cfg = QAConfig.load(profile="lite")
    assert cfg.run.profile == "lite"
    assert cfg.run.cadence_seconds_min == 5.0
