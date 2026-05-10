"""
会话历史存储（SQLite）
每个 chat_id 对应一条记录，messages 存完整 Anthropic messages 格式 JSON
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

logger = logging.getLogger("storage.session")

_DB_PATH = os.path.expanduser("~/sfg_agent/data/sfg_agent.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    chat_id     TEXT PRIMARY KEY,
    messages    TEXT NOT NULL DEFAULT '[]',
    turn_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS session_stats (
    chat_id     TEXT NOT NULL,
    date        TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    avg_ms      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, date)
);
"""


class SessionStore:
    def __init__(self, db_path: str = _DB_PATH, ttl_days: int = 7):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(CREATE_SQL)
        await self._db.commit()
        logger.info("SessionStore 已打开：%s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ──────────────────────────────────────────────
    # 读 / 写
    # ──────────────────────────────────────────────

    async def get_history(self, chat_id: str) -> list[dict[str, Any]]:
        row = await self._db.execute_fetchall(
            "SELECT messages FROM sessions WHERE chat_id = ?", (chat_id,)
        )
        if not row:
            return []
        try:
            return json.loads(row[0][0])
        except Exception:
            return []

    async def save_history(self, chat_id: str, messages: list[dict[str, Any]]) -> None:
        await self._db.execute(
            """
            INSERT INTO sessions (chat_id, messages, turn_count, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                messages   = excluded.messages,
                turn_count = turn_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, json.dumps(messages, ensure_ascii=False), 1),
        )
        await self._db.commit()

    async def clear(self, chat_id: str) -> None:
        await self._db.execute(
            "UPDATE sessions SET messages = '[]', turn_count = 0 WHERE chat_id = ?",
            (chat_id,),
        )
        await self._db.commit()
        logger.info("已清除 %s 的会话历史", chat_id)

    # ──────────────────────────────────────────────
    # 统计
    # ──────────────────────────────────────────────

    async def record_stat(self, chat_id: str, elapsed_ms: int) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        await self._db.execute(
            """
            INSERT INTO session_stats (chat_id, date, count, avg_ms)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(chat_id, date) DO UPDATE SET
                count  = count + 1,
                avg_ms = (avg_ms * count + excluded.avg_ms) / (count + 1)
            """,
            (chat_id, today, elapsed_ms),
        )
        await self._db.commit()

    async def get_stats(self, chat_id: str) -> dict:
        total_row = await self._db.execute_fetchall(
            "SELECT turn_count FROM sessions WHERE chat_id = ?", (chat_id,)
        )
        total = total_row[0][0] if total_row else 0

        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_row = await self._db.execute_fetchall(
            "SELECT count, avg_ms FROM session_stats WHERE chat_id = ? AND date = ?",
            (chat_id, today),
        )
        today_count = today_row[0][0] if today_row else 0
        avg_ms = today_row[0][1] if today_row else 0

        return {"total": total, "today": today_count, "avg_ms": avg_ms}

    # ──────────────────────────────────────────────
    # 压缩：保留最近 N 轮
    # ──────────────────────────────────────────────

    async def compact(self, chat_id: str, keep_turns: int = 10) -> int:
        messages = await self.get_history(chat_id)
        if len(messages) <= keep_turns * 2:
            return 0
        removed = len(messages) - keep_turns * 2
        trimmed = messages[-keep_turns * 2:]
        await self.save_history(chat_id, trimmed)
        logger.info("compact %s：移除 %d 条旧消息", chat_id, removed)
        return removed

    # ──────────────────────────────────────────────
    # TTL 清理（可定期调用）
    # ──────────────────────────────────────────────

    async def purge_expired(self) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=self.ttl_days)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
        )
        await self._db.commit()
        return cur.rowcount
