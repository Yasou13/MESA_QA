from __future__ import annotations

import pytest
from mesa_qa.config import QAConfig


def test_standard_profile_cadence():
    cfg = QAConfig.load(profile="standard")
    assert cfg.run.profile == "standard"
    assert cfg.run.cadence_seconds_min >= 30.0
    assert cfg.run.cadence_seconds_max >= 60.0
    assert cfg.run.cadence_seconds_min <= cfg.run.cadence_seconds_max


def test_all_profiles_have_safe_cadence():
    for prof in ["lite", "standard"]:
        cfg = QAConfig.load(profile=prof)
        assert cfg.run.cadence_seconds_min >= 15.0
        assert cfg.run.cadence_seconds_max >= cfg.run.cadence_seconds_min
