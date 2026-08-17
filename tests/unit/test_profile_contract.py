from __future__ import annotations

import pytest
from mesa_qa.config import QAConfig


def test_stress_behavioral_profile_loads():
    cfg = QAConfig.load(profile="stress-behavioral")
    assert cfg.run.profile == "stress-behavioral"
    assert cfg.run.cadence_seconds_min == 5
    assert cfg.run.cadence_seconds_max == 15
    assert cfg.repair.enabled is False
    assert cfg.resources.sample_seconds == 15


def test_nonexistent_profile_fails_closed():
    with pytest.raises(FileNotFoundError, match="Configuration profile 'ghost' not found"):
        QAConfig.load(profile="ghost")


def test_all_supported_profiles_valid():
    for p in ("lite", "standard", "stress-behavioral"):
        cfg = QAConfig.load(profile=p)
        assert cfg.run.profile == p
        assert cfg.repair.enabled is False
