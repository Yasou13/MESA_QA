from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite


class ControllerDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS run_state (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    planned_end_at TEXT,
                    baseline_main_head TEXT,
                    baseline_main_json TEXT,
                    candidate_base_sha TEXT,
                    candidate_branch TEXT,
                    candidate_head TEXT,
                    candidate_worktree TEXT,
                    qa_storage_root TEXT,
                    current_epoch INT DEFAULT 0,
                    action_count INT DEFAULT 0,
                    confirmed_bug_count INT DEFAULT 0,
                    verified_repair_count INT DEFAULT 0,
                    scenario_cursor INT DEFAULT 0,
                    scenario_seed INT,
                    tester_thread_id TEXT,
                    mesa_pid INT,
                    mcp_gateway_pid INT,
                    last_updated_at TEXT
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS control_requests (
                    run_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    requested_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS action_log (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scenario_event_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    request_json TEXT,
                    response_json TEXT,
                    verdict TEXT NOT NULL,
                    executed_at TEXT NOT NULL
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS anomalies (
                    anomaly_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scenario_event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS bugs (
                    bug_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    bug_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS repairs (
                    repair_id TEXT PRIMARY KEY,
                    bug_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    pre_fix_commit TEXT NOT NULL,
                    post_fix_commit TEXT,
                    files_changed TEXT,
                    tests_run TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            await db.commit()

    async def save_run_state(self, state: Dict[str, Any]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO run_state (
                    run_id, status, started_at, planned_end_at, baseline_main_head, baseline_main_json,
                    candidate_base_sha, candidate_branch, candidate_head, candidate_worktree, qa_storage_root,
                    current_epoch, action_count, confirmed_bug_count, verified_repair_count, last_updated_at,
                    scenario_cursor, scenario_seed, tester_thread_id, mesa_pid, mcp_gateway_pid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    planned_end_at=excluded.planned_end_at,
                    baseline_main_head=excluded.baseline_main_head,
                    baseline_main_json=excluded.baseline_main_json,
                    candidate_base_sha=excluded.candidate_base_sha,
                    candidate_branch=excluded.candidate_branch,
                    candidate_head=excluded.candidate_head,
                    candidate_worktree=excluded.candidate_worktree,
                    qa_storage_root=excluded.qa_storage_root,
                    current_epoch=excluded.current_epoch,
                    action_count=excluded.action_count,
                    confirmed_bug_count=excluded.confirmed_bug_count,
                    verified_repair_count=excluded.verified_repair_count,
                    scenario_cursor=excluded.scenario_cursor,
                    scenario_seed=excluded.scenario_seed,
                    tester_thread_id=excluded.tester_thread_id,
                    mesa_pid=excluded.mesa_pid,
                    mcp_gateway_pid=excluded.mcp_gateway_pid,
                    last_updated_at=datetime('now')
            """,
                (
                    state["run_id"],
                    state["status"],
                    state.get("started_at") or datetime.now(timezone.utc).isoformat(),
                    state.get("planned_end_at"),
                    state.get("baseline_main_head"),
                    state.get("baseline_main_json"),
                    state.get("candidate_base_sha"),
                    state.get("candidate_branch"),
                    state.get("candidate_head"),
                    state.get("candidate_worktree"),
                    state.get("qa_storage_root"),
                    state.get("current_epoch", 0),
                    state.get("action_count", 0),
                    state.get("confirmed_bug_count", 0),
                    state.get("verified_repair_count", 0),
                    state.get("scenario_cursor", 0),
                    state.get("scenario_seed"),
                    state.get("tester_thread_id"),
                    state.get("mesa_pid"),
                    state.get("mcp_gateway_pid"),
                ),
            )
            await db.commit()

    async def get_run_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM run_state WHERE run_id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None

    async def request_control(self, run_id: str, action: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO control_requests(run_id, action) VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET action=excluded.action, requested_at=datetime('now')",
                (run_id, action),
            )
            await db.commit()

    async def get_control(self, run_id: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT action FROM control_requests WHERE run_id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def clear_control(self, run_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM control_requests WHERE run_id = ?", (run_id,))
            await db.commit()

    async def record_action(
        self,
        action_id: str,
        run_id: str,
        scenario_event_id: str,
        action_type: str,
        request: Dict[str, Any],
        response: Dict[str, Any],
        verdict: str,
        executed_at: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO action_log (
                    action_id, run_id, scenario_event_id, action_type, request_json, response_json, verdict, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    action_id,
                    run_id,
                    scenario_event_id,
                    action_type,
                    json.dumps(request),
                    json.dumps(response),
                    verdict,
                    executed_at,
                ),
            )
            await db.commit()

    async def update_action_verdict(
        self,
        action_id: str,
        verdict: str,
        response: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            if response is not None:
                await db.execute(
                    "UPDATE action_log SET verdict = ?, response_json = ? WHERE action_id = ?",
                    (verdict, json.dumps(response), action_id),
                )
            else:
                await db.execute(
                    "UPDATE action_log SET verdict = ? WHERE action_id = ?",
                    (verdict, action_id),
                )
            await db.commit()

    async def list_actions(self, run_id: str) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM action_log WHERE run_id = ? ORDER BY executed_at, action_id",
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        actions: List[Dict[str, Any]] = []
        for row in rows:
            action = dict(row)
            action["request"] = json.loads(action.pop("request_json") or "{}")
            action["response"] = json.loads(action.pop("response_json") or "{}")
            actions.append(action)
        return actions

    async def record_bug(
        self,
        bug_id: str,
        run_id: str,
        severity: str,
        category: str,
        bug_data: Dict[str, Any],
        status: str,
        created_at: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO bugs (bug_id, run_id, severity, category, bug_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bug_id) DO UPDATE SET status=excluded.status
            """,
                (
                    bug_id,
                    run_id,
                    severity,
                    category,
                    json.dumps(bug_data),
                    status,
                    created_at,
                ),
            )
            await db.commit()

    async def get_latest_action(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM action_log WHERE run_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT 1",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    action = dict(row)
                    action["request"] = json.loads(action.pop("request_json") or "{}")
                    action["response"] = json.loads(action.pop("response_json") or "{}")
                    return action
                return None

    async def list_bugs(self, run_id: str) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bugs WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        bugs: List[Dict[str, Any]] = []
        for row in rows:
            b = dict(row)
            b["bug_data"] = json.loads(b.pop("bug_json") or "{}")
            bugs.append(b)
        return bugs

    async def get_full_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        run_state = await self.get_run_state(run_id)
        if not run_state:
            return None
        last_action = await self.get_latest_action(run_id)
        control = await self.get_control(run_id)
        bugs = await self.list_bugs(run_id)

        blocker = None
        if control:
            blocker = f"Control requested: {control}"
        elif run_state.get("status") == "PAUSED":
            blocker = "Session paused by operator"
        elif run_state.get("status") == "WAITING_FOR_CODEX":
            blocker = "Waiting for human approval / Codex interaction"

        return {
            "run_id": run_id,
            "status": run_state.get("status"),
            "started_at": run_state.get("started_at"),
            "last_updated_at": run_state.get("last_updated_at"),
            "planned_end_at": run_state.get("planned_end_at"),
            "candidate_identity": {
                "worktree": run_state.get("candidate_worktree"),
                "branch": run_state.get("candidate_branch"),
                "base_sha": run_state.get("candidate_base_sha"),
                "head": run_state.get("candidate_head"),
                "baseline_main_head": run_state.get("baseline_main_head"),
            },
            "pids": {
                "mesa_pid": run_state.get("mesa_pid"),
                "mcp_gateway_pid": run_state.get("mcp_gateway_pid"),
            },
            "active_action": {
                "current_epoch": run_state.get("current_epoch"),
                "scenario_cursor": run_state.get("scenario_cursor"),
                "action_count": run_state.get("action_count"),
                "tester_thread_id": run_state.get("tester_thread_id"),
            },
            "last_action": last_action,
            "blocker": blocker,
            "bugs": {
                "total": len(bugs),
                "confirmed": run_state.get("confirmed_bug_count", 0),
                "verified": run_state.get("verified_repair_count", 0),
                "items": bugs,
            },
        }
