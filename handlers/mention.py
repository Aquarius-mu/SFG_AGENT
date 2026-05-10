"""
@mention 消息处理器
权限检查 → 斜杠命令 → Agent Loop → 卡片回复
"""
from __future__ import annotations

import asyncio
import logging
import time

from agent.prompt_builder import MessageContext
from gateway.card import CardBuilder
from handlers.slash import handle_slash, is_slash

logger = logging.getLogger("handlers.mention")


def _format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms/1000:.1f}s"
    return f"{ms/60000:.0f}m{(ms%60000)/1000:.0f}s"


async def handle_mention(
    event,
    gateway,
    orchestrator,
    session_store,
    thinking,
    config,
    cache_store,
) -> None:
    start_ms = time.monotonic() * 1000

    # ── 权限检查 ──────────────────────────────────────────────
    user = config.get_user(event.sender_id)
    if user is None:
        await gateway.send_card(event.message_id, CardBuilder.no_permission())
        logger.info("拒绝未授权用户：%s", event.sender_id)
        return

    # ── 斜杠命令 ──────────────────────────────────────────────
    if is_slash(event.text):
        handled = await handle_slash(
            event.text, event, gateway, session_store, thinking
        )
        if handled:
            return

    # ── 读缓存名称（快速，<1ms）─────────────────────────────────
    sender_name = cache_store.get_user_name(event.sender_id) or user.name
    group_name = cache_store.get_group_name(event.chat_id) or "未知群组"

    logger.info(
        "收到 [%s][%s]%s: %s",
        group_name, sender_name,
        ("(续)" if await _has_history(session_store, event.chat_id) else "(首次)"),
        event.text[:80],
    )

    # ── 并行：发"思考中"卡片 + 异步补全缓存 ─────────────────────
    thinking_card = CardBuilder.thinking(sender_name, event.text)
    reply_id_future = asyncio.create_task(
        gateway.send_card(event.message_id, thinking_card)
    )

    # 异步补全用户名/群名（有缓存则跳过）
    asyncio.create_task(_refresh_cache(event, gateway, cache_store))

    # ── 构建上下文 ────────────────────────────────────────────
    ctx = MessageContext(
        chat_id=event.chat_id,
        sender_id=event.sender_id,
        sender_name=sender_name,
        role=user.role,
        message_id=event.message_id,
        group_name=group_name,
        denied_tools=config.get_denied_tools(user.role),
    )

    # ── 调用 Agent ────────────────────────────────────────────
    try:
        answer = await orchestrator.run(event.text, ctx)
        duration = _format_duration(time.monotonic() * 1000 - start_ms)
        final_card = CardBuilder.reply(sender_name, event.text, answer, duration)
    except Exception as exc:
        logger.exception("Agent 执行失败")
        final_card = CardBuilder.error(str(exc), "可发送 /clear 重置会话后重试")

    # ── 更新卡片 ──────────────────────────────────────────────
    reply_id = await reply_id_future
    if reply_id:
        ok = await gateway.update_card(reply_id, final_card)
        if not ok:
            logger.warning("update_card 失败，跳过（避免重复发送）")
    else:
        await gateway.send_card(event.message_id, final_card)

    total_ms = time.monotonic() * 1000 - start_ms
    logger.info(
        "完成 [%s][%s] 总耗时 %s",
        group_name, sender_name, _format_duration(total_ms)
    )


async def _has_history(session_store, chat_id: str) -> bool:
    h = await session_store.get_history(chat_id)
    return bool(h)


async def _refresh_cache(event, gateway, cache_store) -> None:
    """异步刷新用户名/群名缓存（有缓存则跳过）"""
    if not cache_store.get_user_name(event.sender_id):
        try:
            import json as _json
            result = await gateway._run(
                "contact", "users", "get",
                "--params", _json.dumps({"user_id": event.sender_id, "user_id_type": "open_id"}),
                "--as", "user",
                "--jq", ".data.user.name",
            )
            if result and result != "null":
                await cache_store.set_user(event.sender_id, result.strip('"'))
        except Exception:
            pass

    if not cache_store.get_group_name(event.chat_id):
        try:
            import json as _json
            result = await gateway._run(
                "im", "chats", "get",
                "--params", _json.dumps({"chat_id": event.chat_id}),
                "--as", "bot",
                "--jq", ".data.chat.name",
            )
            if result and result != "null":
                await cache_store.set_group(event.chat_id, result.strip('"'))
        except Exception:
            pass
