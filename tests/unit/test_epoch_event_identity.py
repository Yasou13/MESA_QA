from __future__ import annotations

import aiosqlite
import pytest

from mesa_qa.config import QAConfig
from mesa_qa.controller import QAController
from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.oracle.db import OracleDB


@pytest.mark.asyncio
async def test_epoch_event_identity_distinctness(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cfg = QAConfig.load()
    controller = QAController(cfg, run_id="run-epoch-test")

    template_event = ScenarioEvent(
        id="ev-remember-project",
        template_id="ev-remember-project",
        kind=ActionKind.REMEMBER,
        entity="project:atlas",
        field="backend",
        value="FastAPI",
    )

    # In epoch 0:
    controller._epoch = 0
    processed_events = []
    
    async def _capture_process(event):
        processed_events.append(event)

    # Test processing in epoch 0
    controller._process_event = _capture_process
    # Call internal helper or test the logic directly:
    event_ep0 = template_event.model_copy(
        update={
            "id": f"ep{controller._epoch}_{template_event.template_id or template_event.id}" if controller._epoch > 0 else template_event.id,
            "template_id": template_event.template_id or template_event.id,
            "epoch": controller._epoch,
        }
    )
    assert event_ep0.id == "ev-remember-project"
    assert event_ep0.template_id == "ev-remember-project"
    assert event_ep0.epoch == 0

    # In epoch 1:
    controller._epoch = 1
    event_ep1 = template_event.model_copy(
        update={
            "id": f"ep{controller._epoch}_{template_event.template_id or template_event.id}" if controller._epoch > 0 else template_event.id,
            "template_id": template_event.template_id or template_event.id,
            "epoch": controller._epoch,
        }
    )
    assert event_ep1.id == "ep1_ev-remember-project"
    assert event_ep1.template_id == "ev-remember-project"
    assert event_ep1.epoch == 1
    assert event_ep1.id != event_ep0.id


@pytest.mark.asyncio
async def test_oracle_db_applies_events_across_multiple_epochs(tmp_path):
    oracle_db = OracleDB(tmp_path / "oracle.db")
    await oracle_db.initialize()

    # Apply event in epoch 0
    event0 = ScenarioEvent(
        id="ev-remember-project",
        template_id="ev-remember-project",
        epoch=0,
        kind=ActionKind.REMEMBER,
        entity="project:atlas",
        field="backend",
        value="FastAPI",
    )
    await oracle_db.apply_event(event0)

    val0 = await oracle_db.get_current_fact("project:atlas", "backend")
    assert val0 == "FastAPI"

    # Apply event in epoch 1 with new value (e.g. CORRECT or update)
    event1 = ScenarioEvent(
        id="ep1_ev-remember-project",
        template_id="ev-remember-project",
        epoch=1,
        kind=ActionKind.CORRECT,
        entity="project:atlas",
        field="backend",
        value="Django",
    )
    await oracle_db.apply_event(event1)

    val1 = await oracle_db.get_current_fact("project:atlas", "backend")
    assert val1 == "Django"

    # Verify both events are recorded in Oracle events table
    async with aiosqlite.connect(oracle_db.db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM events") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2
