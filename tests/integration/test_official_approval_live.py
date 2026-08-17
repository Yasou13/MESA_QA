from __future__ import annotations

import os
import subprocess
import time

import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.storage.paths import discover_normal_mesa_storage

pytestmark = pytest.mark.skipif(
    os.environ.get("MESA_QA_LIVE_CODEX") != "1",
    reason="set MESA_QA_LIVE_CODEX=1 for the real Codex/MESA approval lifecycle",
)


def event(event_id: str, kind: ActionKind, **values) -> ScenarioEvent:
    return ScenarioEvent(
        id=event_id,
        kind=kind,
        entity="project:atlas",
        field="backend",
        **values,
    )


@pytest.mark.asyncio
async def test_real_tester_official_approval_rotation_and_restart() -> None:
    run_id = f"approval-live-{int(time.time())}"
    config = QAConfig.load(profile="lite")
    config.run.duration_hours = 0.01
    config.repair.enabled = False
    controller = QAController(config=config, run_id=run_id)
    initialized = False
    try:
        await controller.initialize()
        initialized = True
        candidate = controller.process_mgr.candidate_worktree
        assert candidate is not None
        candidate_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=candidate,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        expected_candidate_sha = (
            controller.process_mgr.worktree_mgr.resolve_ref(config.mesa.candidate_ref)
            if config.mesa.candidate_ref
            else subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=config.mesa.repo_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        assert candidate_head == expected_candidate_sha

        events = [
            event(
                "live_remember_fastapi",
                ActionKind.REMEMBER,
                value="FastAPI",
                text="Atlas backend is FastAPI.",
            ),
            event(
                "live_recall_fastapi",
                ActionKind.RECALL,
                question="What backend does Atlas currently use?",
                expected="FastAPI",
            ),
            event(
                "live_paraphrase_fastapi",
                ActionKind.RECALL,
                question="Which server-side framework powers Atlas?",
                expected="FastAPI",
            ),
            event(
                "live_correct_spring",
                ActionKind.CORRECT,
                old_value="FastAPI",
                value="Spring Boot",
                text="Atlas backend is now Spring Boot.",
            ),
            event(
                "live_recall_spring",
                ActionKind.RECALL,
                question="What backend does Atlas currently use?",
                expected="Spring Boot",
            ),
            event("live_rotate_before_recall", ActionKind.ROTATE_SESSION),
            event(
                "live_recall_after_rotation",
                ActionKind.RECALL,
                question="What backend does Atlas currently use?",
                expected="Spring Boot",
            ),
            event("live_restart_candidate", ActionKind.RESTART_RUNTIME),
            event("live_rotate_after_restart", ActionKind.ROTATE_SESSION),
            event(
                "live_recall_after_restart",
                ActionKind.RECALL,
                question="What backend does Atlas currently use?",
                expected="Spring Boot",
            ),
        ]
        for scenario_event in events:
            await controller._process_event(scenario_event)
            actions_so_far = await controller.controller_db.list_actions(run_id)
            latest = next(
                action
                for action in actions_so_far
                if action["scenario_event_id"] == scenario_event.id
            )
            assert latest["verdict"] == "PASS", latest

        actions = await controller.controller_db.list_actions(run_id)
        assert len(actions) == len(events)
        assert all(action["verdict"] == "PASS" for action in actions), actions
        write_actions = [
            action
            for action in actions
            if action["action_type"] in {"remember", "correct"}
        ]
        assert all(
            action["response"]["actual"]["operation_state"] == "COMMITTED"
            for action in write_actions
        )

        qa_storage = (controller.run_dir / "mesa-storage").resolve()
        normal_storage = discover_normal_mesa_storage(config.mesa.repo_path).resolve()
        assert qa_storage != normal_storage
        assert not qa_storage.is_relative_to(normal_storage)
        assert not normal_storage.is_relative_to(qa_storage)
        controller.process_mgr.worktree_mgr.assert_main_unchanged(
            controller._main_baseline
        )
        controller.evidence_store.append_json_record(
            "real_mcp_smoke.json",
            {
                "run_id": run_id,
                "candidate_head": candidate_head,
                "qa_storage": str(qa_storage),
                "normal_storage": str(normal_storage),
                "actions": actions,
                "status": "PASS",
            },
        )
    finally:
        if initialized:
            await controller.shutdown()
        else:
            await controller.process_mgr.stop_all()
        controller.process_mgr.teardown(delete_worktree=True)
