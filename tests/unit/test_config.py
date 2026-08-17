import pytest
from pydantic import ValidationError

from mesa_qa.config import QAConfig, MesaSettings


def test_default_config_loading():
    cfg = QAConfig.load()
    assert cfg.mesa.port == 18000
    assert cfg.mesa.gateway_port == 18765
    assert cfg.safety.max_auto_changed_files == 8
    assert ".github/" in cfg.safety.forbidden_repair_paths
    assert cfg.repair.enabled is False
    assert cfg.approval.enabled is True
    assert cfg.approval.timeout_seconds == 90.0
    assert cfg.mesa.model_enabled is True
    assert cfg.mesa.llm_provider == "mock"
    assert cfg.mesa.validation_mode == 0


def test_lite_profile_loading():
    cfg = QAConfig.load(profile="lite")
    assert cfg.run.profile == "lite"
    assert cfg.run.cadence_seconds_min == 45.0
    assert cfg.run.cadence_seconds_max == 120.0
    assert cfg.repair.enabled is False
    assert cfg.mesa.validation_mode == 0
    assert cfg.mesa.external_provider_enabled is False


def test_validation_mode_parsing():
    for mode_val, expected in [(0, 0), (1, 1), (2, 2), ("0", 0), ("1", 1), ("2", 2), (None, None)]:
        s = MesaSettings(validation_mode=mode_val)
        assert s.validation_mode == expected


def test_invalid_validation_modes_rejected():
    invalid_values = [-1, 3, 10, "3", "-1", "auto", "dual", True, False]
    for inv in invalid_values:
        with pytest.raises(ValidationError):
            MesaSettings(validation_mode=inv)

