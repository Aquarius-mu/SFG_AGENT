"""
跨群工具热力图 v1.0
- 记录每个群（group_id）使用的工具组合（combo）及次数
- 支持查询：哪些 combo 在 ≥N 个不同群中各自出现 ≥M 次
- 用于 Dream.federated_learn 阶段提炼真正通用的技能
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("storage.group_heatmap")

_DB_PATH = Path.home() / "sfg_agent/data/group_heatmap.db"
_COMBO_TTL_DAYS = 180   # 超过 180 天未更新的记录自动清理


@dataclass
class CrossGroupCombo:
    combo_key: str          # 排序后 "|" 连接的工具名
    tool_names: list[str]
    group_count: int        # 出现过该 combo 的不同群数
    total_count: int        # 跨所有群的总触发次数
    last_seen: int          # Unix timestamp


class GroupHeatmap:
    """
    跨群聚合工具热力图。
    设计原则：写入非常轻量（单条 upsert），查询只在 Dream 周期调用。
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or _DB_PATH
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._path))
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS combo_groups (
                combo_key   TEXT    NOT NULL,
                group_id    TEXT    NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1,
                last_seen   INTEGER NOT NULL,
                PRIMARY KEY (combo_key, group_id)
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS learned_combos (
                combo_key   TEXT PRIMARY KEY,
                learned_at  INTEGER NOT NULL
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_combo ON combo_groups(combo_key)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ─── 写入 ─────────────────────────────────────────────────────────────────

    async def record(self, group_id: str, tool_names: list[str]) -> None:
        """记录一次工具组合使用。combo_key = 排序后 | 连接。"""
        if not tool_names or not self._db:
            return
        combo_key = "|".join(sorted(set(tool_names)))
        now = int(time.time())
        async with self._lock:
            await self._db.execute(
                """
                INSERT INTO combo_groups (combo_key, group_id, count, last_seen)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(combo_key, group_id) DO UPDATE SET
                    count    = count + 1,
                    last_seen = excluded.last_seen
                """,
                (combo_key, group_id, now),
            )
            await self._db.commit()

    async def mark_learned(self, combo_key: str) -> None:
        """标记某个 combo 已被 Skillify 学习，避免重复生成。"""
        if not self._db:
            return
        async with self._lock:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO learned_combos (combo_key, learned_at)
                VALUES (?, ?)
                """,
                (combo_key, int(time.time())),
            )
            await self._db.commit()

    # ─── 查询 ─────────────────────────────────────────────────────────────────

    async def get_cross_group_combos(
        self,
        min_groups: int = 3,
        min_total: int = 10,
        limit: int = 20,
    ) -> list[CrossGroupCombo]:
        """
        返回满足条件的跨群高频 combo：
        - 至少在 min_groups 个不同群中出现
        - 跨群总次数 >= min_total
        - 未标记为已学习
        """
        if not self._db:
            return []

        rows = await self._db.execute_fetchall(
            """
            SELECT
                cg.combo_key,
                COUNT(DISTINCT cg.group_id) AS group_count,
                SUM(cg.count)               AS total_count,
                MAX(cg.last_seen)           AS last_seen
            FROM combo_groups cg
            LEFT JOIN learned_combos lc ON lc.combo_key = cg.combo_key
            WHERE lc.combo_key IS NULL
            GROUP BY cg.combo_key
            HAVING group_count >= ? AND total_count >= ?
            ORDER BY total_count DESC
            LIMIT ?
            """,
            (min_groups, min_total, limit),
        )

        result = []
        for row in rows:
            combo_key, group_count, total_count, last_seen = row
            tool_names = combo_key.split("|")
            result.append(CrossGroupCombo(
                combo_key=combo_key,
                tool_names=tool_names,
                group_count=int(group_count),
                total_count=int(total_count),
                last_seen=int(last_seen),
            ))
        return result

    async def get_stats(self) -> dict:
        """返回热力图统计信息。"""
        if not self._db:
            return {}
        total_combos = (await self._db.execute_fetchall(
            "SELECT COUNT(DISTINCT combo_key) FROM combo_groups"
        ))[0][0]
        total_groups = (await self._db.execute_fetchall(
            "SELECT COUNT(DISTINCT group_id) FROM combo_groups"
        ))[0][0]
        learned = (await self._db.execute_fetchall(
            "SELECT COUNT(*) FROM learned_combos"
        ))[0][0]
        return {
            "distinct_combos": total_combos,
            "distinct_groups": total_groups,
            "learned_combos": learned,
        }

    # ─── 维护 ─────────────────────────────────────────────────────────────────

    async def cleanup_stale(self) -> int:
        """删除超过 TTL 天未更新的记录，返回删除行数。"""
        if not self._db:
            return 0
        cutoff = int(time.time()) - _COMBO_TTL_DAYS * 86400
        async with self._lock:
            cursor = await self._db.execute(
                "DELETE FROM combo_groups WHERE last_seen < ?", (cutoff,)
            )
            await self._db.commit()
            return cursor.rowcount
