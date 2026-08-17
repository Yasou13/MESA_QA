"""QA-owned Python environment for a MESA candidate worktree.

The controller itself may run under a different Python release.  Candidate
services must never inherit that interpreter (or the main checkout's .venv),
because migrations, the API runtime, gateway, and repair commands form one
runtime contract.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from mesa_qa.config import CandidateSettings

SUPPORTED_PYTHON_MIN = (3, 10)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 13)


class CandidateEnvironmentError(RuntimeError):
    """Raised before a candidate runtime can be launched safely."""


@dataclass(frozen=True)
class CandidatePythonEnvironment:
    """Resolved base interpreter and QA-owned environment for one run."""

    base_python: Path
    python_bin: Path
    environment_root: Path
    version: str


def python_version(python_bin: Path) -> Tuple[bool, str]:
    """Return supported-range status and interpreter version without sys.executable."""
    result = subprocess.run(
        [
            str(python_bin),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    version = result.stdout.strip()
    try:
        major, minor, _patch = (int(part) for part in version.split(".", 2))
    except ValueError:
        return False, "could not determine Python version"
    supported = SUPPORTED_PYTHON_MIN <= (major, minor) < SUPPORTED_PYTHON_MAX_EXCLUSIVE
    return supported, version


class CandidateEnvironmentManager:
    """Create/reuse the MESA-supported uv environment inside a QA run directory."""

    def __init__(self, settings: CandidateSettings, run_dir: Path):
        self.settings = settings
        self.run_dir = run_dir.resolve()
        self.environment_root = self.run_dir / "candidate-runtime" / "venv"

    def resolve_base_python(self) -> Path:
        """Resolve a configured interpreter, or ask uv for the configured version."""
        if self.settings.python_path is not None:
            python_bin = self.settings.python_path.expanduser().resolve()
            if not python_bin.is_file():
                raise CandidateEnvironmentError(
                    f"Configured candidate Python does not exist: {python_bin}"
                )
            return python_bin

        uv_bin = shutil.which(self.settings.uv_binary)
        if not uv_bin:
            raise CandidateEnvironmentError(
                f"Candidate Python is not configured and uv executable '{self.settings.uv_binary}' is unavailable"
            )
        result = subprocess.run(
            [uv_bin, "python", "find", self.settings.python_version],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        resolved = Path(result.stdout.strip()).expanduser()
        if result.returncode != 0 or not resolved.is_file():
            raise CandidateEnvironmentError(
                "Unable to resolve candidate Python "
                f"{self.settings.python_version!r} using uv: {result.stderr.strip()[-300:]}"
            )
        return resolved.resolve()

    def validate_bootstrap_prerequisites(self, candidate_worktree: Path) -> Path:
        candidate_worktree = candidate_worktree.resolve()
        if not (candidate_worktree / "pyproject.toml").is_file():
            raise CandidateEnvironmentError(
                f"Candidate is missing MESA pyproject.toml: {candidate_worktree}"
            )
        if not (candidate_worktree / "uv.lock").is_file():
            raise CandidateEnvironmentError(
                "Candidate is missing uv.lock; refusing an unlocked dependency bootstrap"
            )
        if not shutil.which(self.settings.uv_binary):
            raise CandidateEnvironmentError(
                f"MESA bootstrap requires uv executable '{self.settings.uv_binary}'"
            )
        return self.resolve_base_python()

    def prepare(self, candidate_worktree: Path) -> CandidatePythonEnvironment:
        """Use MESA's locked uv sync in a per-run environment, never main .venv."""
        candidate_worktree = candidate_worktree.resolve()
        base_python = self.validate_bootstrap_prerequisites(candidate_worktree)
        supported, version = python_version(base_python)
        if not supported:
            raise CandidateEnvironmentError(
                "Unsupported candidate Python runtime "
                f"{version}; MESA-QA requires Python >=3.10,<3.13"
            )

        try:
            self.environment_root.resolve().relative_to(self.run_dir)
        except ValueError as exc:
            raise CandidateEnvironmentError(
                f"Candidate environment escapes QA run directory: {self.environment_root}"
            ) from exc

        uv_bin = shutil.which(self.settings.uv_binary)
        assert uv_bin is not None  # validated above
        candidate_python = self.environment_root / "bin" / "python"
        if not candidate_python.is_file():
            self._run_uv(
                [
                    uv_bin,
                    "venv",
                    str(self.environment_root),
                    "--python",
                    str(base_python),
                ],
                candidate_worktree,
                "create candidate virtual environment",
            )

        # MESA documents uv sync --locked as its dependency/bootstrap mechanism.
        # --active directs uv to the QA-owned VIRTUAL_ENV rather than .venv in the
        # candidate checkout or the original MESA checkout.
        self._run_uv(
            [uv_bin, "sync", "--locked", "--active", "--extra", "dev"],
            candidate_worktree,
            "bootstrap candidate dependencies",
        )

        runtime_supported, runtime_version = python_version(candidate_python)
        if not runtime_supported:
            raise CandidateEnvironmentError(
                "QA-owned candidate environment resolved an unsupported Python "
                f"{runtime_version}; expected >=3.10,<3.13"
            )
        imports = subprocess.run(
            [
                str(candidate_python),
                "-c",
                "import mesa_memory.runtime_entrypoint, mesa_mcp.gateway.app",
            ],
            cwd=candidate_worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=self._environment(),
        )
        if imports.returncode != 0:
            raise CandidateEnvironmentError(
                "Candidate environment is not runtime-ready: "
                f"{imports.stderr.strip()[-500:]}"
            )

        resolved = CandidatePythonEnvironment(
            base_python=base_python,
            # Do not resolve this symlink: its lexical path is what selects the
            # QA-owned virtualenv's site-packages. Resolving it would collapse
            # to the uv-managed base interpreter and lose MESA dependencies.
            python_bin=candidate_python.absolute(),
            environment_root=self.environment_root.resolve(),
            version=runtime_version,
        )
        self._write_evidence(resolved)
        return resolved

    def _environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "VIRTUAL_ENV": str(self.environment_root),
            "PATH": f"{self.environment_root / 'bin'}:{os.environ.get('PATH', '')}",
        }

    def _run_uv(
        self, command: list[str], candidate_worktree: Path, action: str
    ) -> None:
        result = subprocess.run(
            command,
            cwd=candidate_worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=self._environment(),
        )
        log_path = self.environment_root.parent / "bootstrap.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"$ {' '.join(command)}\n{result.stdout}{result.stderr}",
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise CandidateEnvironmentError(
                f"Failed to {action} with MESA's uv bootstrap: "
                f"{(result.stdout + result.stderr).strip()[-800:]}"
            )

    def _write_evidence(self, environment: CandidatePythonEnvironment) -> None:
        evidence_path = self.environment_root.parent / "environment.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "base_python": str(environment.base_python),
                    "candidate_python": str(environment.python_bin),
                    "environment_root": str(environment.environment_root),
                    "version": environment.version,
                    "bootstrap": "uv sync --locked --active --extra dev",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
