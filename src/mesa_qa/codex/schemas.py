from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CodexJSONEvent(BaseModel):
    type: str
    thread_id: Optional[str] = None
    item: Optional[Dict[str, Any]] = None
    tool_call: Optional[Dict[str, Any]] = None
    delta: Optional[str] = None
    message: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CodexRunResult(BaseModel):
    returncode: int
    thread_id: Optional[str] = None
    events: List[CodexJSONEvent] = []
    output_text: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""
