from __future__ import annotations

from pathlib import Path
import yaml
import pytest

from mesa_qa.config import QAConfig


def test_default_profile_repair_disabled():
    cfg = QAConfig.load()
    assert cfg.repair.enabled is False


def test_lite_profile_repair_disabled():
    cfg = QAConfig.load(profile="lite")
    assert cfg.repair.enabled is False


def test_standard_profile_repair_disabled():
    cfg = QAConfig.load(profile="standard")
    assert cfg.repair.enabled is False


def test_all_config_yaml_files_have_repair_disabled():
    config_dir = Path(__file__).parent.parent.parent / "config"
    for yaml_file in config_dir.glob("*.yaml"):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "repair" in data and "enabled" in data["repair"]:
                assert data["repair"]["enabled"] is False, f"Profile {yaml_file.name} has repair.enabled = True!"
