"""
Team Manager v3.0 — GBrain-style 任务路由层
- 关键词快速路由（0 token）：单域任务直接映射到专门工具集
- 动态加载：从 ToolRegistry PROVIDER_META 自动构建路由表，无需硬编码
- 新工具接入只需在 PROVIDER_META 填写 domain_keywords，无需改此文件
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("agent.team_manager")

# ─── 内置默认域（兜底，provider 未加载时使用）────────────────────────────────

@dataclass
class DomainProfile:
    name: str
    keywords: list[str]
    tool_prefixes: list[str]
    focus_hint: str
    weight: int = 1


_DEFAULT_DOMAINS: list[DomainProfile] = [
    DomainProfile(
        name="game",
        keywords=["gm", "区服", "玩家", "发奖", "角色", "服务器"],
        tool_prefixes=["game_", "list_zones", "send_gm", "get_role", "ping_zone"],
        focus_hint="当前任务与游戏服务器运维相关。优先使用 game_tools；执行危险操作前必须确认。",
        weight=3,
    ),
    DomainProfile(
        name="feishu",
        keywords=["飞书", "发消息", "群公告", "文档", "通知", "lark"],
        tool_prefixes=["feishu_", "send_message", "search_group", "get_user_info"],
        focus_hint="当前任务与飞书消息/文档相关。使用 feishu_tools；发消息前确认目标群/用户。",
        weight=2,
    ),
    DomainProfile(
        name="memory",
        keywords=["记住", "忘记", "记忆", "上次", "之前", "历史"],
        tool_prefixes=["remember", "recall", "forget", "search_memory"],
        focus_hint="当前任务与记忆操作相关。使用 memory_tools；写入前先检索是否已存在。",
        weight=2,
    ),
]

_GENERAL_HINT = "当前任务较为综合，使用完整工具集全力处理。"


# ─── 路由结果 ─────────────────────────────────────────────────────────────────

@dataclass
class RoutingResult:
    domain: str
    focus_hint: str
    allowed_tool_prefixes: list[str]
    confidence: float


# ─── TeamManager ─────────────────────────────────────────────────────────────

class TeamManager:
    """
    任务路由器：根据用户输入决定哪个工具域最相关。
    从 ToolRegistry 的 PROVIDER_META 动态构建路由表，
    新增工具模块不需要修改此文件。
    """

    def __init__(self, registry=None):
        domains = self._load_from_registry(registry) if registry else _DEFAULT_DOMAINS
        self._patterns: list[tuple[DomainProfile, re.Pattern]] = []
        for domain in domains:
            if not domain.keywords:
                continue
            pat = re.compile(
                "|".join(re.escape(kw) for kw in domain.keywords),
                re.IGNORECASE,
            )
            self._patterns.append((domain, pat))
        logger.info(
            "TeamManager 路由表: %d 个域（%s）",
            len(self._patterns),
            ", ".join(d.name for d, _ in self._patterns),
        )

    def _load_from_registry(self, registry) -> list[DomainProfile]:
        """从 ToolRegistry PROVIDER_META 动态构建 DomainProfile 列表。"""
        metas = registry.get_provider_metas() if hasattr(registry, "get_provider_metas") else []
        if not metas:
            return _DEFAULT_DOMAINS

        profiles = []
        for meta in metas:
            domain_name = meta.get("domain_name")
            keywords = meta.get("domain_keywords", [])
            if not domain_name or not keywords:
                continue
            profiles.append(DomainProfile(
                name=domain_name,
                keywords=keywords,
                tool_prefixes=meta.get("tool_name_prefixes", []),
                focus_hint=_make_focus_hint(meta),
                weight=meta.get("weight", 1),
            ))

        return profiles if profiles else _DEFAULT_DOMAINS

    def rebuild_from_registry(self, registry) -> None:
        """热更新路由表（新 provider 注册后调用）。"""
        domains = self._load_from_registry(registry)
        self._patterns = []
        for domain in domains:
            if not domain.keywords:
                continue
            pat = re.compile(
                "|".join(re.escape(kw) for kw in domain.keywords),
                re.IGNORECASE,
            )
            self._patterns.append((domain, pat))
        logger.info("TeamManager 路由表已热更新：%d 个域", len(self._patterns))

    def route(self, user_input: str) -> RoutingResult:
        """
        关键词快速路由（0 token）。
        单域命中 → 返回该域路由；多域竞争 → 退回 general。
        """
        scores: dict[str, int] = {}
        for domain, pat in self._patterns:
            matches = pat.findall(user_input)
            if matches:
                scores[domain.name] = len(matches) * domain.weight

        if not scores:
            return RoutingResult(
                domain="general",
                focus_hint=_GENERAL_HINT,
                allowed_tool_prefixes=[],
                confidence=0.5,
            )

        top_name = max(scores, key=scores.__getitem__)
        top_score = scores[top_name]

        rivals = {k: v for k, v in scores.items() if k != top_name and v >= top_score * 0.7}
        if rivals:
            logger.debug("多域竞争 %s，退回 general 路由", scores)
            return RoutingResult(
                domain="general",
                focus_hint=_GENERAL_HINT,
                allowed_tool_prefixes=[],
                confidence=0.4,
            )

        domain_obj = next((d for d, _ in self._patterns if d.name == top_name), None)
        total = sum(scores.values())
        confidence = min(top_score / max(total, 1), 1.0)

        logger.debug("路由到 [%s] score=%d confidence=%.2f", top_name, top_score, confidence)
        return RoutingResult(
            domain=top_name,
            focus_hint=domain_obj.focus_hint if domain_obj else _GENERAL_HINT,
            allowed_tool_prefixes=domain_obj.tool_prefixes if domain_obj else [],
            confidence=confidence,
        )

    def filter_tools(self, all_tools: list, allowed_prefixes: list[str]) -> list:
        """按允许前缀过滤工具列表。空前缀列表 = 允许全部。"""
        if not allowed_prefixes:
            return all_tools

        filtered = []
        for tool in all_tools:
            name = tool.get("name", "") if isinstance(tool, dict) else getattr(tool, "name", "")
            if any(name.startswith(p) or name == p for p in allowed_prefixes):
                filtered.append(tool)

        if not filtered:
            logger.warning("过滤后工具集为空，退回全工具集")
            return all_tools

        return filtered


def _make_focus_hint(meta: dict) -> str:
    """从 PROVIDER_META 生成聚焦提示。"""
    name = meta.get("name", "")
    desc = meta.get("description", "")
    return f"当前任务与 {desc or name} 相关。优先使用 {name} 工具集。"
