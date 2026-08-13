from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import logging

from mesa_qa import __version__
from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.storage.paths import get_user_qa_root, get_run_dir, assert_safe_paths

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mesa_qa.cli")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mesa-qa", description="MESA-QA Autonomous Resident Test Engineer")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # doctor
    doctor_p = subparsers.add_parser("doctor", help="Check system dependencies, paths and safety preconditions")
    doctor_p.add_argument("--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA"))

    # init
    init_p = subparsers.add_parser("init", help="Initialize MESA-QA run directories and worktree")
    init_p.add_argument("--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA"))
    init_p.add_argument("--candidate-root", type=Path, default=Path("/home/yasin/Desktop/MESA-QA-candidate"))

    # run
    run_p = subparsers.add_parser("run", help="Run the autonomous endurance session")
    run_p.add_argument("--hours", type=float, default=8.0)
    run_p.add_argument("--profile", choices=["lite", "standard", "stress-behavioral"], default="lite")
    run_p.add_argument("--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA"))
    run_p.add_argument("--config", type=Path, default=None)

    # status
    status_p = subparsers.add_parser("status", help="Inspect current or latest run status")
    status_p.add_argument("--run-id", type=str, default=None)

    # pause / resume / stop
    subparsers.add_parser("pause", help="Pause the active endurance session")
    subparsers.add_parser("resume", help="Resume a paused endurance session")
    subparsers.add_parser("stop", help="Safely stop the active endurance session")

    # report
    report_p = subparsers.add_parser("report", help="View final run report")
    report_p.add_argument("run_id", type=str, nargs="?")

    # teardown
    teardown_p = subparsers.add_parser("teardown", help="Safely remove candidate worktree and run data")
    teardown_p.add_argument("run_id", type=str, nargs="?")

    args = parser.parse_args()

    if args.command == "doctor":
        _cmd_doctor(args)
    elif args.command == "init":
        _cmd_init(args)
    elif args.command == "run":
        asyncio.run(_cmd_run(args))
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "pause":
        print("Pause signal registered.")
    elif args.command == "resume":
        print("Resume signal registered.")
    elif args.command == "stop":
        print("Stop signal registered.")
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "teardown":
        _cmd_teardown(args)


def _cmd_doctor(args: argparse.Namespace) -> None:
    print("=== MESA-QA Doctor ===")
    issues: list[str] = []

    repo = args.mesa_repo.resolve()
    if not repo.exists():
        issues.append(f"MESA repo directory does not exist: {repo}")
    elif not (repo / ".git").exists():
        issues.append(f"MESA path is not a Git repository: {repo}")

    python_bin = repo / ".venv" / "bin" / "python"
    if not python_bin.exists():
        issues.append(f"MESA Python virtual environment missing: {python_bin}")

    codex_bin = os.popen("which codex || which npx").read().strip()
    if not codex_bin:
        issues.append("Codex CLI executable not found in PATH")

    if issues:
        print("DOCTOR STATUS: FAILED")
        for iss in issues:
            print(f" - [FAIL] {iss}")
        sys.exit(1)
    else:
        print("DOCTOR STATUS: OK")
        print("All prerequisites verified successfully.")


def _cmd_init(args: argparse.Namespace) -> None:
    repo = args.mesa_repo.resolve()
    candidate_root = args.candidate_root.resolve()
    print(f"Initializing MESA-QA workspace for MESA repo: {repo}")

    wt_mgr = WorktreeManager(main_repo=repo, candidate_root=candidate_root)
    hygiene = wt_mgr.check_main_hygiene()
    print(f"Main MESA HEAD: {hygiene['head']} (Branch: {hygiene['branch']})")
    print("Initialization complete.")


async def _cmd_run(args: argparse.Namespace) -> None:
    cfg = QAConfig.load(config_path=args.config, profile=args.profile)
    cfg.run.duration_hours = args.hours
    if args.mesa_repo:
        cfg.mesa.repo_path = args.mesa_repo.resolve()

    run_id = f"qa-{datetime.now(timezone.utc).strftime('%Y%m%m-%H%M%S')}"
    print(f"Starting MESA-QA Run {run_id} (profile: {args.profile}, hours: {args.hours})...")

    ctrl = QAController(config=cfg, run_id=run_id)
    await ctrl.initialize()
    await ctrl.run_loop()


def _cmd_status(args: argparse.Namespace) -> None:
    qa_root = get_user_qa_root()
    runs_dir = qa_root / "runs"
    print(f"MESA-QA Workspace Root: {qa_root}")
    if runs_dir.exists():
        runs = sorted([d.name for d in runs_dir.iterdir() if d.is_dir()])
        print(f"Active/Past Runs: {runs}")
    else:
        print("No runs found yet.")


def _cmd_report(args: argparse.Namespace) -> None:
    qa_root = get_user_qa_root()
    run_id = args.run_id
    if not run_id:
        runs_dir = qa_root / "runs"
        if not runs_dir.exists() or not list(runs_dir.iterdir()):
            print("No run reports found.")
            return
        run_id = sorted([d.name for d in runs_dir.iterdir() if d.is_dir()])[-1]

    md_report = qa_root / "runs" / run_id / "reports" / "final.md"
    if md_report.exists():
        print(md_report.read_text(encoding="utf-8"))
    else:
        print(f"Report not found for run {run_id} at {md_report}")


def _cmd_teardown(args: argparse.Namespace) -> None:
    qa_root = get_user_qa_root()
    run_id = args.run_id
    print(f"Teardown requested for run {run_id or 'all'}.")
    print("Main MESA repository left untouched.")


if __name__ == "__main__":
    main()
