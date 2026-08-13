from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class WorldEntity(BaseModel):
    key: str
    entity_type: str
    attributes: Dict[str, Any] = Field(default_factory=dict)


class SyntheticWorld(BaseModel):
    entities: Dict[str, WorldEntity] = Field(default_factory=dict)

    def add_entity(self, key: str, entity_type: str, attributes: Optional[Dict[str, Any]] = None) -> WorldEntity:
        e = WorldEntity(key=key, entity_type=entity_type, attributes=attributes or {})
        self.entities[key] = e
        return e
