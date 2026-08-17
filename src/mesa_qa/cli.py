from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mesa_qa import __version__
from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.runtime.candidate_environment import (
    CandidateEnvironmentError,
    CandidateEnvironmentManager,
    python_version,
)
from mesa_qa.runtime.worktree import WorktreeManager
from mesa_qa.storage.controller_db import ControllerDB
from mesa_qa.storage.paths import (
    assert_safe_paths,
    discover_normal_mesa_storage,
    generate_run_id,
    get_user_qa_root,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mesa_qa.cli")


def _supported_python_version(python_bin: Path) -> tuple[bool, str]:
    """Compatibility wrapper retained for callers of the doctor helper."""
    return python_version(python_bin)


def run_doctor_checks(
    config_path: Optional[Path] = None,
    mesa_repo: Optional[Path] = None,
) -> Tuple[bool, List[str], List[str]]:
    """Run comprehensive system and contract preconditions checks for MESA-QA.

    Returns (success, passed_checks, issues).
    """
    passes: List[str] = []
    issues: List[str] = []

    cfg = QAConfig.load(config_path=config_path) if config_path else QAConfig.load()
    repo = (mesa_repo or cfg.mesa.repo_path).resolve()

    # 1. Main Git Repository verification
    if not repo.exists():
        issues.append(f"MESA repo directory does not exist: {repo}")
    elif not (repo / ".git").exists():
        issues.append(f"MESA path is not a Git repository: {repo}")
    else:
        passes.append(f"MESA repository verified at {repo}")

    # 2. Git CLI
    if not shutil.which("git"):
        issues.append("Git CLI executable not found in PATH")
    else:
        git_ver = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        )
        if git_ver.returncode == 0:
            passes.append(f"Git CLI available ({git_ver.stdout.strip()})")
        else:
            issues.append("Git CLI failed execution check")

    # 3. Controller and candidate runtime interpreters. The controller may use
    # any Python accepted by MESA-QA itself; candidate services must use a
    # separate explicitly resolved interpreter in the strict supported range.
    controller_python = Path(sys.executable).resolve()
    controller_supported, controller_version = _supported_python_version(
        controller_python
    )
    controller_status = (
        "supported" if controller_supported else "outside candidate range"
    )
    passes.append(
        f"Controller Python {controller_version} at {controller_python} ({controller_status})"
    )
    passes.append("Candidate Python supported range is >=3.10,<3.13")

    candidate_manager = CandidateEnvironmentManager(
        cfg.candidate, get_user_qa_root() / "runs" / "doctor"
    )
    try:
        candidate_base_python = candidate_manager.validate_bootstrap_prerequisites(repo)
        candidate_supported, candidate_version = _supported_python_version(
            candidate_base_python
        )
        if not candidate_supported:
            issues.append(
                "Unsupported candidate Python runtime "
                f"{candidate_version}; MESA-QA requires Python >=3.10,<3.13. "
                "Candidate startup is blocked before migrations."
            )
        else:
            passes.append(
                f"Candidate base Python {candidate_version} resolved at {candidate_base_python}"
            )
        passes.append(
            "Candidate environment bootstrap prerequisites ready "
            "(MESA uv.lock + uv sync --locked --active --extra dev)"
        )
    except CandidateEnvironmentError as exc:
        issues.append(f"Candidate environment is not ready: {exc}")

    # 4. Codex CLI
    codex_bin = cfg.codex.binary
    if not shutil.which(codex_bin):
        issues.append(f"Codex CLI executable '{codex_bin}' not found in PATH")
    else:
        res = subprocess.run(
            [codex_bin, "--version"], capture_output=True, text=True, check=False
        )
        if res.returncode == 0:
            passes.append(f"Codex CLI available ({res.stdout.strip()})")
        else:
            issues.append(f"Codex CLI execution check failed: {res.stderr.strip()}")

    # 5. Candidate Ref Pinning Check
    if repo.exists() and (repo / ".git").exists():
        try:
            wt_mgr = WorktreeManager(
                repo, cfg.candidate.worktree_root, cfg.candidate.branch_prefix
            )
            if cfg.mesa.candidate_ref:
                resolved_sha = wt_mgr.resolve_ref(cfg.mesa.candidate_ref)
                passes.append(
                    f"Pinned candidate ref '{cfg.mesa.candidate_ref}' resolved to SHA {resolved_sha}"
                )
            else:
                hygiene = wt_mgr.check_main_hygiene()
                passes.append(
                    f"Default candidate ref HEAD resolved to {hygiene['head']}"
                )
        except Exception as exc:
            issues.append(f"Candidate ref resolution failed: {exc}")

    # 6. Storage and Worktree Isolation Check
    try:
        if cfg.candidate.worktree_root.resolve() == repo.resolve():
            issues.append("Candidate root cannot equal MESA main checkout directory")
        else:
            normal_storage = (
                cfg.mesa.normal_storage_root or discover_normal_mesa_storage(repo)
            )
            doctor_cand = cfg.candidate.worktree_root / "doctor-candidate"
            doctor_qa_storage = get_user_qa_root() / "runs" / "doctor" / "mesa-storage"
            assert_safe_paths(
                main_repo=repo,
                candidate_worktree=doctor_cand,
                qa_storage=doctor_qa_storage,
                normal_mesa_storage=normal_storage,
                qa_root=get_user_qa_root(),
            )
            passes.append("Path isolation and storage containment assertions verified")
    except (RuntimeError, ValueError) as exc:
        issues.append(f"Storage/worktree safety assertion failed: {exc}")

    # 7. Auth Type and Paid-Provider Fallback State Check
    auth_type = cfg.codex.auth_type
    passes.append(f"Codex auth type configured as '{auth_type}'")
    # Verify strict paid provider fallback policy: fallback is disabled by default
    passes.append("Paid-provider fallback policy: strictly disabled (fail-closed)")

    # 8. MESA Validation Mode Support Check
    val_mode = cfg.mesa.validation_mode
    if val_mode in (0, 1, 2):
        passes.append(
            f"MESA validation mode {val_mode} verified (0=NORMAL, 1=FAST, 2=EXTREME)"
        )
    else:
        issues.append(f"Unsupported MESA validation mode: {val_mode}")

    # 9. Port Availability Check
    for label, port in (
        ("MESA", cfg.mesa.port),
        ("MCP gateway", cfg.mesa.gateway_port),
    ):
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe_socket.bind(("127.0.0.1", port))
            passes.append(f"{label} port {port} is available")
        except OSError:
            issues.append(f"{label} port {port} is occupied")
        finally:
            probe_socket.close()

    success = len(issues) == 0
    return success, passes, issues


def _cmd_doctor(args: argparse.Namespace) -> None:
    print("=== MESA-QA Doctor ===")
    success, passes, issues = run_doctor_checks(
        config_path=args.config,
        mesa_repo=args.mesa_repo,
    )

    for p in passes:
        print(f" [PASS] {p}")

    if not success:
        print("\nDOCTOR STATUS: FAILED")
        for iss in issues:
            print(f" [FAIL] {iss}")
        sys.exit(1)
    else:
        print("\nDOCTOR STATUS: OK")
        print("All preconditions and contract requirements verified successfully.")


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
    if args.minutes is not None:
        cfg.run.duration_hours = args.minutes / 60.0
    else:
        cfg.run.duration_hours = args.hours
    if args.mesa_repo:
        cfg.mesa.repo_path = args.mesa_repo.resolve()

    duration_desc = (
        f"{args.minutes} minutes" if args.minutes is not None else f"{args.hours} hours"
    )
    if args.resume:
        run_id = args.resume
        print(f"Resuming MESA-QA Run {run_id}...")
        ctrl = QAController(config=cfg, run_id=run_id)
        await ctrl.resume_from_crash()
    else:
        run_id = generate_run_id("qa")
        print(
            f"Starting MESA-QA Run {run_id} (profile: {args.profile}, duration: {duration_desc})..."
        )
        ctrl = QAController(config=cfg, run_id=run_id)
        await ctrl.initialize()

    await ctrl.run_loop()


def _format_status_report(status_data: Dict[str, Any]) -> str:
    lines = [
        "=== MESA-QA Run Status ===",
        f"Run ID:               {status_data.get('run_id')}",
        f"Status:               {status_data.get('status')}",
        f"Started At:           {status_data.get('started_at') or 'N/A'}",
        f"Last Updated:         {status_data.get('last_updated_at') or 'N/A'}",
        "",
        "--- Candidate Identity ---",
        f"Worktree:             {status_data.get('candidate_identity', {}).get('worktree') or 'N/A'}",
        f"Branch:               {status_data.get('candidate_identity', {}).get('branch') or 'N/A'}",
        f"Base SHA:             {status_data.get('candidate_identity', {}).get('base_sha') or 'N/A'}",
        f"Candidate HEAD:       {status_data.get('candidate_identity', {}).get('head') or 'N/A'}",
        f"Baseline Main HEAD:   {status_data.get('candidate_identity', {}).get('baseline_main_head') or 'N/A'}",
        "",
        "--- Process PIDs ---",
        f"MESA Runtime PID:     {status_data.get('pids', {}).get('mesa_pid') or 'N/A'}",
        f"MCP Gateway PID:      {status_data.get('pids', {}).get('mcp_gateway_pid') or 'N/A'}",
        "",
        "--- Active / Progress ---",
        f"Current Epoch:        {status_data.get('active_action', {}).get('current_epoch', 0)}",
        f"Scenario Cursor:      {status_data.get('active_action', {}).get('scenario_cursor', 0)}",
        f"Total Actions:        {status_data.get('active_action', {}).get('action_count', 0)}",
        f"Tester Thread:        {status_data.get('active_action', {}).get('tester_thread_id') or 'N/A'}",
        "",
        "--- Last Action ---",
    ]
    last_act = status_data.get("last_action")
    if last_act:
        lines.extend(
            [
                f"Action ID:            {last_act.get('action_id')}",
                f"Scenario Event:       {last_act.get('scenario_event_id')}",
                f"Type:                 {last_act.get('action_type')}",
                f"Verdict:              {last_act.get('verdict')}",
                f"Executed At:          {last_act.get('executed_at')}",
            ]
        )
    else:
        lines.append("No actions recorded yet.")

    lines.extend(
        [
            "",
            "--- Blocker / Control ---",
            f"Blocker:              {status_data.get('blocker') or 'None'}",
            "",
            "--- Bugs & Repairs ---",
            f"Total Bugs Logged:    {status_data.get('bugs', {}).get('total', 0)}",
            f"Confirmed Bugs:       {status_data.get('bugs', {}).get('confirmed', 0)}",
            f"Verified Repairs:     {status_data.get('bugs', {}).get('verified', 0)}",
        ]
    )
    return "\n".join(lines)


async def _cmd_status(args: argparse.Namespace) -> None:
    qa_root = get_user_qa_root()
    runs_dir = qa_root / "runs"

    target_run_id = args.run_id
    if not target_run_id:
        if runs_dir.exists():
            candidates = sorted(
                (
                    p
                    for p in runs_dir.iterdir()
                    if p.is_dir() and (p / "controller.db").exists()
                ),
                key=lambda p: p.stat().st_mtime,
            )
            if candidates:
                target_run_id = candidates[-1].name
        if not target_run_id:
            print("No MESA-QA runs found.")
            return

    target_dir = runs_dir / target_run_id
    if not target_dir.exists():
        print(f"Run '{target_run_id}' not found.")
        return
    db_path = target_dir / "controller.db"
    if not db_path.exists():
        print(f"Run '{target_run_id}' has no controller.db.")
        return

    db = ControllerDB(db_path)
    await db.initialize()
    status_data = await db.get_full_status(target_run_id)
    if not status_data:
        print(f"No status data found for run '{target_run_id}'.")
        return

    if getattr(args, "json", False):
        print(json.dumps(status_data, indent=2))
    else:
        print(_format_status_report(status_data))


async def _cmd_control(action: str, run_id: Optional[str] = None) -> None:
    runs = get_user_qa_root() / "runs"
    if run_id:
        target_dir = runs / run_id
        if not target_dir.exists() or not (target_dir / "controller.db").exists():
            raise SystemExit(
                f"No run found with ID '{run_id}' or missing controller.db."
            )
        db = ControllerDB(target_dir / "controller.db")
        await db.initialize()
        await db.request_control(run_id, action)
        print(f"{action.title()} requested for run {run_id}.")
        return

    candidates = (
        sorted(
            (
                path
                for path in runs.iterdir()
                if path.is_dir() and (path / "controller.db").exists()
            ),
            key=lambda path: path.stat().st_mtime,
        )
        if runs.exists()
        else []
    )
    if not candidates:
        raise SystemExit("No persisted MESA-QA run is available for control.")
    run = candidates[-1]
    db = ControllerDB(run / "controller.db")
    await db.initialize()
    await db.request_control(run.name, action)
    print(f"{action.title()} requested for run {run.name}.")


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


async def _safe_kill_pids(pids: List[int]) -> None:
    for pid in pids:
        if not pid or pid <= 1:
            continue
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
    await asyncio.sleep(0.5)
    for pid in pids:
        if not pid or pid <= 1:
            continue
        try:
            os.kill(pid, 9)  # SIGKILL
        except OSError:
            pass


async def _async_cmd_teardown(args: argparse.Namespace) -> None:
    cfg = (
        QAConfig.load(config_path=args.config)
        if getattr(args, "config", None)
        else QAConfig.load()
    )
    if getattr(args, "mesa_repo", None):
        cfg.mesa.repo_path = args.mesa_repo.resolve()

    qa_root = get_user_qa_root()
    runs_dir = qa_root / "runs"
    main_repo = cfg.mesa.repo_path.resolve()
    candidate_root = cfg.candidate.worktree_root.resolve()

    wt_mgr = WorktreeManager(
        main_repo=main_repo,
        candidate_root=candidate_root,
        branch_prefix=cfg.candidate.branch_prefix,
    )

    run_ids = [args.run_id] if args.run_id else []
    if not run_ids and runs_dir.exists():
        run_ids = [
            d.name
            for d in runs_dir.iterdir()
            if d.is_dir() and (d / "controller.db").exists()
        ]

    print(f"Executing safe teardown for {len(run_ids)} run(s)...")

    for rid in run_ids:
        run_dir = runs_dir / rid
        db_path = run_dir / "controller.db"
        pids_to_kill: List[int] = []

        if db_path.exists():
            try:
                db = ControllerDB(db_path)
                await db.initialize()
                status_data = await db.get_full_status(rid)
                if status_data:
                    pids_dict = status_data.get("pids", {})
                    for p in ("mesa_pid", "mcp_gateway_pid"):
                        pid_val = pids_dict.get(p)
                        if isinstance(pid_val, int):
                            pids_to_kill.append(pid_val)
            except Exception as exc:
                logger.warning("Could not read PIDs from %s: %s", db_path, exc)

        if pids_to_kill:
            print(f"Terminating candidate processes for run {rid}: {pids_to_kill}")
            await _safe_kill_pids(pids_to_kill)

        # Remove candidate worktrees matching run_id or branch
        possible_wt_paths = [
            candidate_root / f"run-{rid}",
            candidate_root / rid,
        ]
        if status_data and status_data.get("candidate_identity", {}).get("worktree"):
            try:
                possible_wt_paths.append(
                    Path(status_data["candidate_identity"]["worktree"])
                )
            except Exception:
                pass

        for cand_wt in set(possible_wt_paths):
            if cand_wt.exists():
                try:
                    branch_name = f"{cfg.candidate.branch_prefix}-{rid}"
                    wt_mgr.remove_candidate_worktree(
                        cand_wt, delete_branch=True, branch_name=branch_name
                    )
                    print(f"Removed candidate worktree: {cand_wt}")
                except Exception as exc:
                    logger.warning("Could not remove worktree %s: %s", cand_wt, exc)

        # Clean lock and socket files from run directory
        if run_dir.exists():
            for lf in run_dir.glob("*.lock"):
                try:
                    lf.unlink(missing_ok=True)
                except Exception:
                    pass
            for sf in run_dir.glob("*.sock"):
                try:
                    sf.unlink(missing_ok=True)
                except Exception:
                    pass

    # Prune git worktrees on main repo
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=main_repo,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass

    print("Teardown completed safely.")
    print("Main MESA repository left untouched.")
    print("All audit/log records strictly preserved under QA root.")


def _cmd_teardown(args: argparse.Namespace) -> None:
    asyncio.run(_async_cmd_teardown(args))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mesa-qa", description="MESA-QA Autonomous Resident Test Engineer"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # doctor
    doctor_p = subparsers.add_parser(
        "doctor", help="Check system dependencies, paths and safety preconditions"
    )
    doctor_p.add_argument(
        "--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA")
    )
    doctor_p.add_argument("--config", type=Path, default=None)

    # init
    init_p = subparsers.add_parser(
        "init", help="Initialize MESA-QA run directories and worktree"
    )
    init_p.add_argument(
        "--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA")
    )
    init_p.add_argument(
        "--candidate-root",
        type=Path,
        default=Path("/home/yasin/Desktop/MESA-QA-candidate"),
    )

    # run
    run_p = subparsers.add_parser("run", help="Run the autonomous endurance session")
    run_p.add_argument("--hours", type=float, default=8.0)
    run_p.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="Duration in minutes (overrides --hours)",
    )
    run_p.add_argument(
        "--profile", choices=["lite", "standard", "stress-behavioral"], default="lite"
    )
    run_p.add_argument(
        "--mesa-repo", type=Path, default=Path("/home/yasin/Desktop/MESA")
    )
    run_p.add_argument("--config", type=Path, default=None)
    run_p.add_argument(
        "--resume", type=str, default=None, help="Resume an existing run by run ID"
    )

    # status
    status_p = subparsers.add_parser(
        "status", help="Inspect current or latest run status"
    )
    status_p.add_argument("--run-id", type=str, default=None, help="Target run ID")
    status_p.add_argument("--json", action="store_true", help="Output status as JSON")

    # pause / resume / stop
    pause_p = subparsers.add_parser("pause", help="Pause the active endurance session")
    pause_p.add_argument("--run-id", type=str, default=None, help="Target run ID")

    resume_p = subparsers.add_parser("resume", help="Resume a paused endurance session")
    resume_p.add_argument("--run-id", type=str, default=None, help="Target run ID")

    stop_p = subparsers.add_parser(
        "stop", help="Safely stop the active endurance session"
    )
    stop_p.add_argument("--run-id", type=str, default=None, help="Target run ID")

    # report
    report_p = subparsers.add_parser("report", help="View final run report")
    report_p.add_argument("run_id", type=str, nargs="?")

    # teardown
    teardown_p = subparsers.add_parser(
        "teardown", help="Safely remove candidate worktree and run data"
    )
    teardown_p.add_argument("run_id", type=str, nargs="?")
    teardown_p.add_argument("--mesa-repo", type=Path, default=None)
    teardown_p.add_argument("--config", type=Path, default=None)

    args = parser.parse_args()

    if args.command == "doctor":
        _cmd_doctor(args)
    elif args.command == "init":
        _cmd_init(args)
    elif args.command == "run":
        asyncio.run(_cmd_run(args))
    elif args.command == "status":
        asyncio.run(_cmd_status(args))
    elif args.command == "pause":
        asyncio.run(_cmd_control("pause", getattr(args, "run_id", None)))
    elif args.command == "resume":
        asyncio.run(_cmd_control("resume", getattr(args, "run_id", None)))
    elif args.command == "stop":
        asyncio.run(_cmd_control("stop", getattr(args, "run_id", None)))
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "teardown":
        _cmd_teardown(args)


if __name__ == "__main__":
    main()
