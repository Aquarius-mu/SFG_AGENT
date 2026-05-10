"""
Hermes-style Prompt Builder v2.0
- 平台感知 system prompt
- 技能强制注入（frontmatter 过滤后）
- Prompt injection 检测（上下文文件安全扫描）
- 记忆上下文 fence（<memory-context>）
- 角色感知权限提示
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("agent.prompt_builder")

# ──────────────────────────────────────────────────────────
# 注入检测模式（Hermes security scanning）
# ──────────────────────────────────────────────────────────
_INJECTION_PATTERNS = [
    re.compile(r'[​-‏‪-‮﻿]'),  # 不可见 Unicode
    re.compile(r'<!--[\s\S]*?-->', re.I),                # HTML 注释
    re.compile(r'ignore\s+(previous|all|above)\s+instructions', re.I),
    re.compile(r'you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?(?:unrestricted|jailbroken)', re.I),
    re.compile(r'exfiltrate|leak.*credentials|send.*api.?key', re.I),
    re.compile(r'<\|.*?\|>', re.I),                      # 控制 token 伪装
]

# ──────────────────────────────────────────────────────────
# Prompt 常量
# ──────────────────────────────────────────────────────────

_BASE_IDENTITY = """你是 SFG_AGENT，一个部署在飞书群的 AI 游戏运营助手，由 Anthropic Claude 驱动。
你服务于游戏公司的 QA、运营和策划团队，可以帮助他们查询游戏数据、执行 GM 指令、操作飞书文档。"""

_FEISHU_PLATFORM = """
## 飞书平台规则（必须遵守）
- 回答使用飞书 Markdown（lark_md），不使用标准 Markdown 语法
- 卡片使用 schema 2.0；折叠面板颜色必须带层级后缀（blue-100，不能用 blue）
- @提及语法：`<at id="ou_xxx">姓名</at>`
- Emoji 只能用官方 key：:DONE: :Loudspeaker: :Trophy: :Fire:，禁用 :WAITING:
- 回答超过 1500 字时，主动提示用 /compact 压缩历史
"""

_GAME_CONTEXT = """
## 游戏服务器环境
- 大型 C++ 游戏服务器（GameServer/FightServer/AllianceServer）
- 开发环境 env=d，区服 ID 为整数
- 执行 GM 指令前先确认操作意图；批量/复制/广播等危险操作需二次确认
- 可调用 list_zones 工具查看当前可用区服列表
"""

_SKILL_RULE = """
## 技能加载规则（强制）
以下是已加载的技能文档。**如果某个技能与当前任务相关，你必须优先参考该技能再作答，不得跳过。**
技能文档包含经过验证的最佳实践，优先级高于通用知识。
"""

_OPERATOR_ONLY = """
## 当前用户权限
你正在与 **{role}** 用户对话。以下工具对该用户不可用：{denied}。
如果用户请求超出权限的操作，礼貌说明并建议联系管理员。
"""

_TOOL_DISCIPLINE = """
## 工具使用规范
- 只在确实需要外部数据时调用工具，不要为了展示能力而无谓调用
- 工具调用前先说明用途；工具返回错误时，告知用户并提供替代建议
- 危险操作（批量 GM、角色复制）必须先复述操作内容，等待用户确认后再执行
"""


@dataclass
class MessageContext:
    chat_id: str
    sender_id: str
    sender_name: str
    role: str
    message_id: str
    group_name: str = "未知群组"
    denied_tools: list = field(default_factory=list)


class PromptBuilder:
    def __init__(self, config, skill_loader=None):
        self.config = config
        self.skill_loader = skill_loader
        self._game_enabled = getattr(config.game, "enabled", False) if hasattr(config, "game") else False

    def build(self, ctx: MessageContext, memory_ctx: Optional[str] = None,
              focus_hint: Optional[str] = None) -> list[dict]:
        """
        构建带 Prompt Caching 的 system prompt。
        返回 list[dict]，Anthropic SDK 原生支持此格式。
        静态部分（identity/platform/game/tools/skills）加 cache_control，
        动态部分（memory/per-turn info）不缓存。
        """
        static_parts = [_BASE_IDENTITY, _FEISHU_PLATFORM]

        if self._game_enabled and ctx.role in ("admin", "operator"):
            static_parts.append(_GAME_CONTEXT)

        static_parts.append(_TOOL_DISCIPLINE)

        if focus_hint:
            static_parts.append(f"## 任务聚焦\n{focus_hint}")

        if self.skill_loader:
            skills_text = self.skill_loader.get_relevant_skills()
            if skills_text:
                safe_skills = _scan_and_sanitize(skills_text)
                if safe_skills:
                    static_parts.append(_SKILL_RULE + "\n" + safe_skills)

        static_text = "\n\n".join(static_parts)

        # 动态部分（每次对话都不同，不缓存）
        dynamic_parts = []

        if ctx.denied_tools:
            denied_str = "、".join(ctx.denied_tools[:5])
            dynamic_parts.append(_OPERATOR_ONLY.format(role=ctx.role, denied=denied_str))

        if memory_ctx:
            safe_memory = _scan_and_sanitize(memory_ctx)
            if safe_memory:
                dynamic_parts.append(safe_memory)

        dynamic_parts.append(
            f"## 当前对话\n"
            f"- 群组：{ctx.group_name}（chat_id: {ctx.chat_id}）\n"
            f"- 用户：{ctx.sender_name}（open_id: {ctx.sender_id}）\n"
            f"- 角色：{ctx.role}"
        )

        result: list[dict] = [
            {
                "type": "text",
                "text": static_text,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if dynamic_parts:
            result.append({
                "type": "text",
                "text": "\n\n".join(dynamic_parts),
            })

        return result


def _scan_and_sanitize(text: str) -> str:
    """
    Hermes security scanning：检测 prompt injection 模式。
    发现可疑内容时记录警告并移除该段，而非静默注入。
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning("Prompt injection 检测：发现可疑内容，已过滤（pattern=%s）", pattern.pattern[:40])
            # 移除匹配到的部分
            text = pattern.sub("[REMOVED]", text)
    return text
