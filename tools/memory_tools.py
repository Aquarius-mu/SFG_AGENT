"""
记忆工具集（Phase 3）
GBrain brain-ops: remember / recall / search_memory / forget
后端：storage/memory_store.py（SQLite FTS5 + 三层命名空间 + RRF 融合）
"""

PROVIDER_META: dict = {
    "name": "memory_tools",
    "description": "长期记忆存储与检索工具",
    "register_fn": "register_memory_tools",
    "domain_name": "memory",
    "domain_keywords": [
        "记住", "忘记", "记忆", "上次", "之前", "历史", "remember",
        "recall", "forget", "记录", "存档", "search_memory", "查记忆",
    ],
    "tool_name_prefixes": ["remember", "recall", "forget", "search_memory"],
    "weight": 2,
    "requires_config_key": "memory.enabled",
}
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.memory_manager import MemoryManager
    from tools.registry import ToolRegistry

logger = logging.getLogger("tools.memory")


def register_memory_tools(registry: "ToolRegistry", memory_mgr: "MemoryManager") -> None:
    """注册记忆工具到 registry，并绑定 MemoryManager。"""

    @registry.register(
        name="remember",
        description=(
            "将重要信息存入长期记忆。适合存储：用户偏好、群运营习惯、GM 指令套路、"
            "常用区服信息、上次操作的结论等。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容（清晰、完整的一句话）",
                },
                "entity": {
                    "type": "string",
                    "description": "关联实体名（如玩家ID、区服名、功能名），留空则自动提取",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分类标签，如 ['gm', 'zone', 'user_pref']",
                },
            },
            "required": ["content"],
        },
        is_deterministic=False,
    )
    async def remember(
        content: str,
        entity: str = "",
        tags: list[str] | None = None,
    ) -> dict:
        ok = await memory_mgr.remember(content, entity=entity, tags=tags or [])
        return {"ok": ok, "stored": content[:100]}

    @registry.register(
        name="recall",
        description=(
            "从长期记忆中检索相关信息。使用三层命名空间（global/group/user）+"
            "FTS5 关键词检索 + RRF 融合排序，优先返回高相关度记忆。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或问题描述",
                },
                "top_k": {
                    "type": "integer",
                    "description": "最多返回条数（默认 5，最大 20）",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        is_deterministic=True,
    )
    async def recall(query: str, top_k: int = 5) -> dict:
        top_k = min(top_k, 20)
        items = await memory_mgr.recall(query, top_k=top_k)
        return {"memories": items, "total": len(items)}

    @registry.register(
        name="search_memory",
        description=(
            "全文搜索记忆库，支持中文分词检索。"
            "比 recall 更精确，适合查找特定操作记录或错误模式。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（支持中文）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "最多返回条数（默认 10）",
                    "default": 10,
                },
                "entity_type": {
                    "type": "string",
                    "description": (
                        "按实体类型过滤（可选）："
                        "error_pattern / zone / person / concept / game_event / general"
                    ),
                },
            },
            "required": ["query"],
        },
        is_deterministic=True,
    )
    async def search_memory(
        query: str, top_k: int = 10, entity_type: str = ""
    ) -> dict:
        top_k = min(top_k, 20)
        items = await memory_mgr.recall(query, top_k=top_k)
        if entity_type:
            items = [i for i in items if i.get("entity_type") == entity_type]
        return {"memories": items, "total": len(items)}

    @registry.register(
        name="forget",
        description="从记忆库中删除指定 ID 的记忆条目。",
        input_schema={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "记忆条目的 ID（从 recall/search_memory 结果中获取）",
                },
            },
            "required": ["memory_id"],
        },
        is_deterministic=False,
        allowed_roles=["admin", "operator"],
    )
    async def forget(memory_id: int) -> dict:
        ok = await memory_mgr.delete(memory_id)
        if ok:
            return {"ok": True, "deleted_id": memory_id}
        return {"ok": False, "error": "记忆后端未启用或删除失败"}

    @registry.register(
        name="list_error_patterns",
        description="查询 GM 指令失败归因记录（创新③：GM 失败自动记录）。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "指令名或错误关键词（可选）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "最多返回条数（默认 10）",
                    "default": 10,
                },
            },
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator"],
    )
    async def list_error_patterns(keyword: str = "", top_k: int = 10) -> dict:
        query = keyword or "GM 失败 错误"
        items = await memory_mgr.recall(query, top_k=top_k)
        error_items = [i for i in items if i.get("entity_type") == "error_pattern"]
        return {
            "error_patterns": error_items,
            "total": len(error_items),
            "hint": "这些是之前 GM 指令失败时自动记录的原因和正确做法。",
        }
