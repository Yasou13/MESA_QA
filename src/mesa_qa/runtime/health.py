from __future__ import annotations

import httpx
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger("mesa_qa.health")


async def check_mesa_health(base_url: str, api_key: Optional[str] = None, timeout: float = 5.0) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/health/init"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            headers = {}
            if api_key:
                headers["X-API-Key"] = api_key
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return {"status": "healthy", "code": 200, "data": resp.json()}
            return {"status": "unhealthy", "code": resp.status_code, "detail": resp.text}
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}


async def check_mcp_gateway_health(gateway_url: str, bearer_token: Optional[str] = None, timeout: float = 5.0) -> Dict[str, Any]:
    url = f"{gateway_url.rstrip('/')}/mcp/v1/health"
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (200, 401):
                # 401 means endpoint reached but token missing/invalid; 200 means healthy
                return {"status": "healthy" if resp.status_code == 200 else "auth_required", "code": resp.status_code}
            return {"status": "unhealthy", "code": resp.status_code, "detail": resp.text}
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}
