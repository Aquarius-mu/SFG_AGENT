"""
飞书 Gateway：事件监听 + 消息发送
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("gateway.feishu")


@dataclass
class FeishuEvent:
    message_id: str
    sender_id: str
    chat_id: str
    text: str
    raw: dict


class FeishuGateway:
    def __init__(self, config):
        self.lark_cli = config.lark.lark_cli_path or "lark-cli"
        self.bot_open_id = config.lark.bot_open_id

    # ──────────────────────────────────────────────
    # 内部：异步执行 lark-cli 命令
    # ──────────────────────────────────────────────

    async def _run(self, *args: str, input_data: Optional[str] = None, timeout: float = 30.0) -> str:
        cmd = [self.lark_cli] + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input_data else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_data.encode() if input_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"lark-cli 超时（{timeout}s）：{' '.join(args[:3])}")
        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.warning("lark-cli error: %s", err)
        return stdout.decode().strip()

    # ──────────────────────────────────────────────
    # 发送 / 更新卡片
    # ──────────────────────────────────────────────

    async def send_card(self, message_id: str, card: dict) -> Optional[str]:
        """回复消息，返回新消息 ID（用于后续更新）"""
        content = json.dumps(card, ensure_ascii=False)
        t0 = time.monotonic()
        result = await self._run(
            "im", "+messages-reply",
            "--message-id", message_id,
            "--msg-type", "interactive",
            "--content", content,
            "--as", "bot",
            "--jq", ".data.message_id",
        )
        logger.debug("send_card %.0fms", (time.monotonic() - t0) * 1000)
        return result.strip('"') if result else None

    async def update_card(self, message_id: str, card: dict) -> bool:
        """原地更新已发出的卡片"""
        if not message_id:
            return False
        payload = json.dumps(
            {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            ensure_ascii=False,
        )
        t0 = time.monotonic()
        result = await self._run(
            "api", "PATCH", f"/open-apis/im/v1/messages/{message_id}",
            "--data", payload,
            "--as", "bot",
        )
        logger.debug("update_card %.0fms", (time.monotonic() - t0) * 1000)
        try:
            data = json.loads(result)
            return data.get("code", -1) == 0
        except Exception:
            return False

    # ──────────────────────────────────────────────
    # 事件订阅主循环
    # ──────────────────────────────────────────────

    async def subscribe(self, queue: asyncio.Queue) -> None:
        """持续监听飞书事件流，解析后投入 queue"""
        processed: set[str] = set()

        while True:
            logger.info("连接飞书事件流...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.lark_cli, "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--quiet",
                    "--as", "bot",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async for raw_line in proc.stdout:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    event = self._parse_event(line, processed)
                    if event:
                        await queue.put(event)

            except Exception as exc:
                logger.warning("事件流异常：%s", exc)

            logger.info("断开，5 秒后重连…")
            await asyncio.sleep(5)

    def _parse_event(self, line: str, processed: set) -> Optional[FeishuEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        msg = data.get("event", {}).get("message", {})
        sender = data.get("event", {}).get("sender", {})

        chat_id = msg.get("chat_id", "")
        message_id = msg.get("message_id", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "")

        if not all([chat_id, message_id, sender_id]):
            return None

        # 去重
        if message_id in processed:
            return None
        processed.add(message_id)
        # 防内存泄漏：超过 10000 条清一半
        if len(processed) > 10000:
            to_remove = list(processed)[:5000]
            for mid in to_remove:
                processed.discard(mid)

        # 必须 @Bot
        mentions = msg.get("mentions", [])
        if not any(m.get("id", {}).get("open_id") == self.bot_open_id for m in mentions):
            return None

        # 解析文字内容
        raw_content = msg.get("content", "{}")
        try:
            content = json.loads(raw_content)
        except Exception:
            content = {}
        text = content.get("text", "")
        # 去掉 @提及 标记
        for mention in mentions:
            key = mention.get("key", "")
            if key:
                text = text.replace(key, "")
        text = text.strip()
        if not text:
            return None

        return FeishuEvent(
            message_id=message_id,
            sender_id=sender_id,
            chat_id=chat_id,
            text=text,
            raw=data,
        )
