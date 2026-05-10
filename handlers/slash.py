"""
斜杠命令处理器
/clear /think /compact /stats /help
"""
from __future__ import annotations

import logging

from gateway.card import CardBuilder

logger = logging.getLogger("handlers.slash")

_SLASH_COMMANDS = {"/clear", "/think", "/compact", "/stats", "/help", "清除记忆", "帮助"}


def is_slash(text: str) -> bool:
    first_word = text.split()[0] if text.split() else ""
    return first_word in _SLASH_COMMANDS


async def handle_slash(
    text: str,
    event,
    gateway,
    session_store,
    thinking,
) -> bool:
    """
    处理斜杠命令，返回 True 表示已处理，无需进入 agent loop
    """
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    # ── /clear / 清除记忆 ──────────────────────────────────────
    if cmd in ("/clear", "清除记忆"):
        await session_store.clear(event.chat_id)
        thinking.reset(event.chat_id)
        card = CardBuilder.success("🗑️ 已清除", "对话历史已清除，思考模式已重置。下一条消息将开启新会话。")
        await gateway.send_card(event.message_id, card)
        return True

    # ── /help / 帮助 ───────────────────────────────────────────
    if cmd in ("/help", "帮助"):
        await gateway.send_card(event.message_id, CardBuilder.help())
        return True

    # ── /think <level> ────────────────────────────────────────
    if cmd == "/think":
        level = args[0].lower() if args else ""
        if level not in ("low", "medium", "high"):
            card = CardBuilder.error(
                f"无效的思考级别：{level or '(空)'}",
                "用法：/think low | medium | high",
            )
            await gateway.send_card(event.message_id, card)
            return True
        thinking.set_level(event.chat_id, level)
        desc = {"low": "关闭深度推理（最快）", "medium": "适度推理", "high": "深度推理（复杂分析）"}
        card = CardBuilder.success(
            f"💭 思考模式：{level}",
            f"{desc[level]}\n\n此设置对当前群组生效，/clear 后重置。",
        )
        await gateway.send_card(event.message_id, card)
        return True

    # ── /compact ──────────────────────────────────────────────
    if cmd == "/compact":
        removed = await session_store.compact(event.chat_id)
        if removed > 0:
            card = CardBuilder.success("🗜️ 已压缩", f"已移除 {removed} 条旧消息，保留最近 10 轮对话。")
        else:
            card = CardBuilder.success("🗜️ 无需压缩", "对话历史较短，无需压缩。")
        await gateway.send_card(event.message_id, card)
        return True

    # ── /stats ────────────────────────────────────────────────
    if cmd == "/stats":
        st = await session_store.get_stats(event.chat_id)
        card = CardBuilder.stats(
            event.chat_id,
            total=st["total"],
            today=st["today"],
            avg_ms=st["avg_ms"],
        )
        await gateway.send_card(event.message_id, card)
        return True

    return False
