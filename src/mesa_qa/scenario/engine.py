from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import random
import yaml
import logging

from mesa_qa.models import ActionKind, ScenarioEvent

logger = logging.getLogger("mesa_qa.scenario_engine")


class ScenarioEngine:
    def __init__(self, scenarios_dir: Path, seed: int = 42):
        self.scenarios_dir = scenarios_dir.resolve()
        self.seed = seed
        self.events: List[ScenarioEvent] = []
        self._cursor: int = 0

    def load_suite(self, scenario_names: Optional[List[str]] = None) -> int:
        self.events.clear()
        self._cursor = 0

        target_files = []
        if scenario_names:
            for name in scenario_names:
                fn = name if name.endswith(".yaml") else f"{name}.yaml"
                fp = self.scenarios_dir / fn
                if fp.exists():
                    target_files.append(fp)
        else:
            target_files = sorted(list(self.scenarios_dir.glob("*.yaml")))

        generator = random.Random(self.seed)
        for fp in target_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    raw_events = data.get("events", [])
                    for ev_dict in raw_events:
                        event = ScenarioEvent(
                            id=ev_dict["id"],
                            kind=ActionKind(ev_dict["kind"]),
                            entity=ev_dict.get("entity", "global"),
                            field=ev_dict.get("field"),
                            value=ev_dict.get("value"),
                            old_value=ev_dict.get("old_value"),
                            text=ev_dict.get("text"),
                            question=ev_dict.get("question"),
                            mode=ev_dict.get("mode", "current"),
                            expected=ev_dict.get("expected"),
                            # A seed controls generated scenario identity while preserving
                            # the declared causal ordering of YAML events.
                            effective_at=ev_dict.get("effective_at", f"qa-seed-{self.seed}-{generator.randrange(1_000_000_000):09d}"),
                        )
                        self.events.append(event)
            except Exception as exc:
                logger.error("Failed to load scenario file %s: %s", fp, exc)

        logger.info("Loaded %d events across %d scenario files", len(self.events), len(target_files))
        return len(self.events)

    def has_next(self) -> bool:
        return self._cursor < len(self.events)

    def next_event(self) -> Optional[ScenarioEvent]:
        if not self.has_next():
            return None
        ev = self.events[self._cursor]
        self._cursor += 1
        return ev

    def reset(self) -> None:
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor
