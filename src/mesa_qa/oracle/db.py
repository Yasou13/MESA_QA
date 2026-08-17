from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite

from mesa_qa.models import ActionKind, ScenarioEvent
from mesa_qa.oracle.schema import ORACLE_SCHEMA_SQL


class OracleDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(ORACLE_SCHEMA_SQL)
            await db.commit()

    async def apply_event(self, event: ScenarioEvent) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Event IDs are durable applied markers. A crash/replay must not
            # duplicate the corresponding fact side effects.
            async with db.execute("SELECT 1 FROM events WHERE event_id = ?", (event.id,)) as cursor:
                if await cursor.fetchone():
                    return
            # 1. Log event
            await db.execute(
                "INSERT INTO events (event_id, kind, payload_json, applied_at) VALUES (?, ?, ?, ?)",
                (event.id, event.kind.value, json.dumps(event.model_dump()), now),
            )

            # Ensure entity exists
            await db.execute(
                "INSERT OR IGNORE INTO entities (entity_key, entity_type, created_at) VALUES (?, ?, ?)",
                (event.entity, event.entity.split(":")[0] if ":" in event.entity else "entity", now),
            )

            if event.kind in {
                ActionKind.REMEMBER,
                ActionKind.DUPLICATE,
                ActionKind.SEMANTIC_DUPLICATE,
                ActionKind.IDEMPOTENCY,
            }:
                field_name = event.field or "general"
                async with db.execute(
                    "SELECT fact_id, value_json FROM facts WHERE entity_key = ? AND field = ? AND status = 'CURRENT'",
                    (event.entity, field_name),
                ) as cursor:
                    existing = await cursor.fetchone()

                if existing:
                    fact_id = existing[0]
                    await db.execute(
                        """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (fact_id, event.entity, field_name, json.dumps(event.value), event.kind.value.upper(), event.id, now),
                    )
                else:
                    fact_id = f"fact_{event.id}"
                    await db.execute(
                        """INSERT INTO facts (fact_id, entity_key, field, value_json, status, valid_from, source_event_id)
                           VALUES (?, ?, ?, ?, 'CURRENT', ?, ?)""",
                        (fact_id, event.entity, field_name, json.dumps(event.value), now, event.id),
                    )
                    await db.execute(
                        """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (fact_id, event.entity, field_name, json.dumps(event.value), event.kind.value.upper(), event.id, now),
                    )

            elif event.kind in {ActionKind.CORRECT, ActionKind.CONFLICT}:
                field_name = event.field or "general"
                await db.execute(
                    "UPDATE facts SET status = 'HISTORICAL', valid_to = ? WHERE entity_key = ? AND field = ? AND status = 'CURRENT'",
                    (now, event.entity, field_name),
                )
                fact_id = f"fact_{event.id}"
                await db.execute(
                    """INSERT INTO facts (fact_id, entity_key, field, value_json, status, valid_from, source_event_id)
                       VALUES (?, ?, ?, ?, 'CURRENT', ?, ?)""",
                    (fact_id, event.entity, field_name, json.dumps(event.value), now, event.id),
                )
                await db.execute(
                    """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (fact_id, event.entity, field_name, json.dumps(event.value), event.kind.value.upper(), event.id, now),
                )

            elif event.kind == ActionKind.MULTI_FACT:
                if isinstance(event.value, dict):
                    for k, v in event.value.items():
                        sub_fact_id = f"fact_{event.id}_{k}"
                        await db.execute(
                            """INSERT INTO facts (fact_id, entity_key, field, value_json, status, valid_from, source_event_id)
                               VALUES (?, ?, ?, ?, 'CURRENT', ?, ?)""",
                            (sub_fact_id, event.entity, k, json.dumps(v), now, event.id),
                        )
                        await db.execute(
                            """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                               VALUES (?, ?, ?, ?, 'MULTI_FACT', ?, ?)""",
                            (sub_fact_id, event.entity, k, json.dumps(v), event.id, now),
                        )
                else:
                    fact_id = f"fact_{event.id}"
                    field_name = event.field or "general"
                    await db.execute(
                        """INSERT INTO facts (fact_id, entity_key, field, value_json, status, valid_from, source_event_id)
                           VALUES (?, ?, ?, ?, 'CURRENT', ?, ?)""",
                        (fact_id, event.entity, field_name, json.dumps(event.value), now, event.id),
                    )
                    await db.execute(
                        """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                           VALUES (?, ?, ?, ?, 'MULTI_FACT', ?, ?)""",
                        (fact_id, event.entity, field_name, json.dumps(event.value), event.id, now),
                    )

            elif event.kind == ActionKind.FORGET:
                field_name = event.field or "general"
                await db.execute(
                    "UPDATE facts SET status = 'FORGOTTEN', valid_to = ? WHERE entity_key = ? AND field = ? AND status = 'CURRENT'",
                    (now, event.entity, field_name),
                )
                await db.execute(
                    """INSERT INTO fact_history (fact_id, entity_key, field, value_json, action, event_id, recorded_at)
                       VALUES (?, ?, ?, ?, 'FORGET', ?, ?)""",
                    (f"forget_{event.id}", event.entity, field_name, json.dumps(None), event.id, now),
                )

            await db.commit()

    async def get_current_fact(self, entity: str, field: str) -> Optional[Any]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value_json FROM facts WHERE entity_key = ? AND field = ? AND status = 'CURRENT' ORDER BY valid_from DESC LIMIT 1",
                (entity, field),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row[0])
                return None

    async def get_fact_history(self, entity: str, field: str) -> List[Any]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value_json FROM fact_history WHERE entity_key = ? AND field = ? ORDER BY id ASC",
                (entity, field),
            ) as cursor:
                rows = await cursor.fetchall()
                return [json.loads(r[0]) for r in rows if r[0] is not None]

    async def get_historical_facts(self, entity: str, field: str) -> List[Any]:
        """Return previous historical values for entity+field that have been replaced/corrected, excluding CURRENT."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value_json FROM facts WHERE entity_key = ? AND field = ? AND status = 'HISTORICAL' ORDER BY valid_from ASC",
                (entity, field),
            ) as cursor:
                rows = await cursor.fetchall()
                return [json.loads(r[0]) for r in rows if r[0] is not None]

    async def is_forgotten(self, entity: str, field: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            # If there is an active CURRENT fact, it is NOT forgotten
            async with db.execute(
                "SELECT 1 FROM facts WHERE entity_key = ? AND field = ? AND status = 'CURRENT' LIMIT 1",
                (entity, field),
            ) as cursor:
                if await cursor.fetchone():
                    return False

            # Check if the most recent history action was FORGET
            async with db.execute(
                "SELECT action FROM fact_history WHERE entity_key = ? AND field = ? ORDER BY id DESC LIMIT 1",
                (entity, field),
            ) as cursor:
                row = await cursor.fetchone()
                return bool(row and row[0] == "FORGET")
