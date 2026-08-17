from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MesaSettings(StrictBaseModel):
    repo_path: Path = Field(default=Path("/home/yasin/Desktop/MESA"))
    python_path: Path = Field(default=Path("/home/yasin/Desktop/MESA/.venv/bin/python"))
    runtime_profile: str = "combined"
    port: int = 18000
    gateway_port: int = 18765
    normal_storage_root: Optional[Path] = None
    model_enabled: bool = True
    external_provider_enabled: bool = True
    llm_provider: str = "mock"
    validation_mode: Optional[int] = Field(default=0)
    candidate_ref: Optional[str] = Field(default=None)

    @field_validator("validation_mode", mode="before")
    @classmethod
    def validate_validation_mode(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str:
                return None
            if not (v_str.isdigit() or (v_str.startswith("-") and v_str[1:].isdigit())):
                raise ValueError(
                    f"validation_mode must be 0, 1, or 2; got '{v}'"
                )
            v = int(v_str)
        elif isinstance(v, bool):
            raise ValueError(
                f"validation_mode must be an integer (0, 1, 2); got boolean {v}"
            )
        elif not isinstance(v, int):
            raise ValueError(
                f"validation_mode must be an integer (0, 1, 2); got {type(v).__name__}"
            )
        if v not in (0, 1, 2):
            raise ValueError(f"validation_mode must be 0, 1, or 2; got {v}")
        return v


class CandidateSettings(StrictBaseModel):
    worktree_root: Path = Field(default=Path("/home/yasin/Desktop/MESA-QA-candidate"))
    branch_prefix: str = "qa/autonomous"


class RunSettings(StrictBaseModel):
    duration_hours: float = 8.0
    profile: str = "lite"
    seed: int = 42
    cadence_seconds_min: float = 45.0
    cadence_seconds_max: float = 120.0


class CodexSettings(StrictBaseModel):
    binary: str = "codex"
    tester_model: Optional[str] = None
    repair_model: Optional[str] = None
    tester_timeout_seconds: int = 300
    repair_timeout_seconds: int = 1200
    json_events: bool = True
    auth_type: str = "local"


class ApprovalSettings(StrictBaseModel):
    enabled: bool = True
    operator_principal: str = "mesa-qa-operator"
    timeout_seconds: float = 90.0
    poll_interval_seconds: float = 2.0


class RepairSettings(StrictBaseModel):
    enabled: bool = False
    max_repairs_per_run: int = 10
    require_pre_fix_failure: bool = True
    commit_verified_repairs: bool = True
    auto_push: bool = False
    auto_merge: bool = False


class VerificationSettings(StrictBaseModel):
    targeted_tests_only_per_fix: bool = True
    full_suite_at_end: bool = True
    full_suite_every_n_repairs: int = 3
    run_full_suite: bool = False


class ResourcesSettings(StrictBaseModel):
    sample_seconds: int = 60
    warn_rss_mb: int = 6000
    hard_stop_rss_mb: int = 12000


class SafetySettings(StrictBaseModel):
    require_clean_main: bool = True
    forbid_main_branch_write: bool = True
    forbid_network_in_repair: bool = True
    max_auto_changed_files: int = 8
    max_auto_changed_lines: int = 400
    forbidden_repair_paths: List[str] = Field(
        default_factory=lambda: [".github/", "deploy/", "uv.lock", "pyproject.toml"]
    )


class QAConfig(StrictBaseModel):
    mesa: MesaSettings = Field(default_factory=MesaSettings)
    candidate: CandidateSettings = Field(default_factory=CandidateSettings)
    run: RunSettings = Field(default_factory=RunSettings)
    codex: CodexSettings = Field(default_factory=CodexSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    repair: RepairSettings = Field(default_factory=RepairSettings)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)
    resources: ResourcesSettings = Field(default_factory=ResourcesSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)

    @classmethod
    def load(
        cls, config_path: Optional[Path] = None, profile: Optional[str] = None
    ) -> QAConfig:
        config_data: dict[str, Any] = {}

        # 1. Load default.yaml if exists
        default_yaml = Path(__file__).parent.parent.parent / "config" / "default.yaml"
        if default_yaml.exists():
            with open(default_yaml, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

        # 2. Load profile yaml (lite/standard/stress-behavioral) if specified
        if profile:
            profile_yaml = (
                Path(__file__).parent.parent.parent / "config" / f"{profile}.yaml"
            )
            if not profile_yaml.exists():
                raise FileNotFoundError(
                    f"Configuration profile '{profile}' not found at {profile_yaml}"
                )
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
