from __future__ import annotations

ORACLE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    entity_key TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    entity_key TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    status TEXT NOT NULL, -- CURRENT, HISTORICAL, FORGOTTEN
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source_event_id TEXT NOT NULL,
    FOREIGN KEY(entity_key) REFERENCES entities(entity_key)
);

CREATE TABLE IF NOT EXISTS fact_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    action TEXT NOT NULL, -- REMEMBER, CORRECT, FORGET
    event_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""
