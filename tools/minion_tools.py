"""
Minion 工具集
GBrain Minion 路由：确定性工具通过 asyncio 队列执行（$0 · 无 LLM）
本模块注册的工具全部 is_deterministic=True，由 orchestrator 路由到 MinionQueue。
"""

PROVIDER_META: dict = {
    "name": "minion_tools",
    "description": "Minion 确定性工具（区服探活等）",
    "register_fn": "register_minion_tools",
    "domain_name": None,          # Minion 工具不单独路由，随其他域一起使用
    "domain_keywords": [],
    "tool_name_prefixes": ["ping_zone"],
    "weight": 1,
    "requires_config_key": None,
}
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.minion_queue import MinionQueue
    from tools.registry import ToolRegistry

logger = logging.getLogger("tools.minion")


def register_minion_tools(registry: "ToolRegistry", queue: "MinionQueue") -> None:
    """注册 Minion 工具到 registry，并绑定 MinionQueue。"""

    @registry.register(
        name="ping_zone",
        description="检测指定区服 GM 后台是否可达，返回响应时间（毫秒）。",
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "integer", "description": "区服 ID"},
            },
            "required": ["zone_id"],
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator", "viewer"],
    )
    async def ping_zone(zone_id: int) -> dict:
        import time as _time
        from tools.game_tools import _load_config, _build_auth_query, _build_cookies
        import httpx

        try:
            cfg = _load_config()
            base_url = cfg["base_url"].rstrip("/")
            url = f"{base_url}/api.php"
            t0 = _time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    params={"mod": "GmTools", "act": "zonelist", **_build_auth_query(cfg)},
                    cookies=_build_cookies(cfg),
                )
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                return {
                    "zone_id": zone_id,
                    "reachable": resp.status_code == 200,
                    "http_status": resp.status_code,
                    "elapsed_ms": elapsed_ms,
                }
        except Exception as e:
            return {"zone_id": zone_id, "reachable": False, "error": str(e)}

    @registry.register(
        name="get_minion_stats",
        description="获取 Minion 队列当前统计信息（提交数/完成数/失败数/队列大小）。",
        input_schema={
            "type": "object",
            "properties": {},
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator"],
    )
    async def get_minion_stats() -> dict:
        return queue.get_stats()

    @registry.register(
        name="batch_ping_zones",
        description="批量检测多个区服的 GM 后台可达性，通过 Minion 队列并发执行。",
        input_schema={
            "type": "object",
            "properties": {
                "zone_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要检测的区服 ID 列表",
                },
            },
            "required": ["zone_ids"],
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator"],
    )
    async def batch_ping_zones(zone_ids: list[int]) -> dict:
        import asyncio as _asyncio
        from tools.game_tools import _load_config, _build_auth_query, _build_cookies
        import httpx
        import time as _time

        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        base_url = cfg["base_url"].rstrip("/")
        url = f"{base_url}/api.php"

        async def _ping(zone_id: int) -> dict:
            t0 = _time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        url,
                        params={"mod": "GmTools", "act": "zonelist", **_build_auth_query(cfg)},
                        cookies=_build_cookies(cfg),
                    )
                    elapsed_ms = int((_time.monotonic() - t0) * 1000)
                    return {
                        "zone_id": zone_id,
                        "reachable": resp.status_code == 200,
                        "elapsed_ms": elapsed_ms,
                    }
            except Exception as e:
                return {"zone_id": zone_id, "reachable": False, "error": str(e)}

        results = await _asyncio.gather(*[_ping(z) for z in zone_ids])
        reachable = sum(1 for r in results if r.get("reachable"))
        return {
            "results": list(results),
            "reachable": reachable,
            "total": len(zone_ids),
        }
