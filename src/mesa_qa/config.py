from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional
import yaml
from pydantic import BaseModel, Field


class MesaSettings(BaseModel):
    repo_path: Path = Field(default=Path("/home/yasin/Desktop/MESA"))
    python_path: Path = Field(default=Path("/home/yasin/Desktop/MESA/.venv/bin/python"))
    runtime_profile: str = "combined"
    port: int = 18000
    gateway_port: int = 18765


class CandidateSettings(BaseModel):
    worktree_root: Path = Field(default=Path("/home/yasin/Desktop/MESA-QA-candidate"))
    branch_prefix: str = "qa/autonomous"
    reuse_existing: bool = False


class RunSettings(BaseModel):
    duration_hours: float = 8.0
    profile: str = "lite"
    seed: int = 42
    cadence_seconds_min: float = 5.0
    cadence_seconds_max: float = 15.0
    epoch_actions: int = 25
    restart_every_minutes: float = 90.0
    parallel_actions: int = 1


class CodexSettings(BaseModel):
    binary: str = "codex"
    tester_model: Optional[str] = None
    repair_model: Optional[str] = None
    tester_timeout_seconds: int = 300
    repair_timeout_seconds: int = 1200
    json_events: bool = True


class RepairSettings(BaseModel):
    enabled: bool = True
    max_repairs_per_run: int = 10
    require_pre_fix_failure: bool = True
    commit_verified_repairs: bool = True
    auto_push: bool = False
    auto_merge: bool = False


class VerificationSettings(BaseModel):
    targeted_tests_only_per_fix: bool = True
    full_suite_at_end: bool = True
    full_suite_every_n_repairs: int = 3


class ResourcesSettings(BaseModel):
    sample_seconds: int = 60
    warn_rss_mb: int = 6000
    hard_stop_rss_mb: int = 12000


class SafetySettings(BaseModel):
    require_clean_main: bool = True
    forbid_main_branch_write: bool = True
    forbid_network_in_repair: bool = True
    max_auto_changed_files: int = 8
    max_auto_changed_lines: int = 400
    forbidden_repair_paths: List[str] = Field(
        default_factory=lambda: [".github/", "deploy/", "uv.lock", "pyproject.toml"]
    )


class QAConfig(BaseModel):
    mesa: MesaSettings = Field(default_factory=MesaSettings)
    candidate: CandidateSettings = Field(default_factory=CandidateSettings)
    run: RunSettings = Field(default_factory=RunSettings)
    codex: CodexSettings = Field(default_factory=CodexSettings)
    repair: RepairSettings = Field(default_factory=RepairSettings)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)
    resources: ResourcesSettings = Field(default_factory=ResourcesSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)

    @classmethod
    def load(cls, config_path: Optional[Path] = None, profile: Optional[str] = None) -> QAConfig:
        config_data: dict[str, Any] = {}
        
        # 1. Load default.yaml if exists
        default_yaml = Path(__file__).parent.parent.parent / "config" / "default.yaml"
        if default_yaml.exists():
            with open(default_yaml, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

        # 2. Load profile yaml (lite/standard) if specified
        if profile:
            profile_yaml = Path(__file__).parent.parent.parent / "config" / f"{profile}.yaml"
            if profile_yaml.exists():
                with open(profile_yaml, "r", encoding="utf-8") as f:
                    pdata = yaml.safe_load(f) or {}
                    _deep_merge(config_data, pdata)

        # 3. Load explicit config_path if passed
        if config_path and config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
                _deep_merge(config_data, cdata)

        return cls.model_validate(config_data)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
