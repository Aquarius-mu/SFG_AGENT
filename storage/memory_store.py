"""
GBrain-style 混合记忆存储 v2.1
- SQLite FTS5 trigram（中文子串搜索）
- Compiled Truth + Timeline 模型
- 创新②：三层命名空间（global / group / user）+ RRF 加权融合
- 创新③：GM 失败归因记忆（entity_type=error_pattern）
- RRF 融合排序（为向量搜索预留接口）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

logger = logging.getLogger("storage.memory")

_DB_PATH = os.path.expanduser("~/sfg_agent/data/sfg_agent.db")

# 三层命名空间权重（user > group > global）
_NS_WEIGHTS = {"user": 3.0, "group": 2.0, "global": 1.0}

# ──────────────────────────────────────────────────────────
# DDL
# ──────────────────────────────────────────────────────────

_CREATE_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity          TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'concept',
    namespace       TEXT NOT NULL DEFAULT 'global',
    ns_id           TEXT NOT NULL DEFAULT '',
    compiled_truth  TEXT NOT NULL,
    timeline        TEXT NOT NULL DEFAULT '[]',
    tags            TEXT NOT NULL DEFAULT '[]',
    backlinks       TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_key ON memories(entity, namespace, ns_id);
CREATE INDEX IF NOT EXISTS idx_memories_ns ON memories(namespace, ns_id);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(entity_type);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    entity,
    compiled_truth,
    tags,
    content='memories',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, entity, compiled_truth, tags)
    VALUES (new.id, new.entity, new.compiled_truth, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, entity, compiled_truth, tags)
    VALUES ('delete', old.id, old.entity, old.compiled_truth, old.tags);
    INSERT INTO memories_fts(rowid, entity, compiled_truth, tags)
    VALUES (new.id, new.entity, new.compiled_truth, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, entity, compiled_truth, tags)
    VALUES ('delete', old.id, old.entity, old.compiled_truth, old.tags);
END;
"""

_ENTITY_TYPES = {"person", "zone", "event", "concept", "group", "error_pattern"}

# ──────────────────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    id: int
    entity: str
    entity_type: str
    namespace: str          # global / group / user
    ns_id: str              # group chat_id 或 user open_id
    compiled_truth: str
    timeline: list[str]
    tags: list[str]
    backlinks: list[str]
    created_at: float
    updated_at: float
    score: float = 0.0

    def to_context_line(self) -> str:
        prefix = {
            "error_pattern": "⚠️ 失败案例",
            "person": "👤",
            "zone": "🗺️ 区服",
            "event": "📅 事件",
        }.get(self.entity_type, "")
        return f"{prefix} {self.entity}：{self.compiled_truth}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity": self.entity,
            "entity_type": self.entity_type,
            "namespace": self.namespace,
            "compiled_truth": self.compiled_truth,
            "timeline": self.timeline,
            "tags": self.tags,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: tuple) -> "MemoryItem":
        (id_, entity, entity_type, namespace, ns_id, compiled_truth,
         timeline_j, tags_j, backlinks_j, created_at, updated_at) = row[:11]
        return cls(
            id=id_, entity=entity, entity_type=entity_type,
            namespace=namespace, ns_id=ns_id,
            compiled_truth=compiled_truth,
            timeline=json.loads(timeline_j or "[]"),
            tags=json.loads(tags_j or "[]"),
            backlinks=json.loads(backlinks_j or "[]"),
            created_at=created_at, updated_at=updated_at,
        )


# ──────────────────────────────────────────────────────────
# HybridMemoryStore
# ──────────────────────────────────────────────────────────

class HybridMemoryStore:
    def __init__(self, db_path: str = _DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_CREATE_MEMORY_SQL)
        await self._db.commit()
        logger.info("HybridMemoryStore 已打开：%s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ──────────────────────────────────────────────
    # 写入
    # ──────────────────────────────────────────────

    async def upsert(
        self,
        entity: str,
        new_info: str,
        entity_type: str = "concept",
        tags: list[str] | None = None,
        namespace: str = "global",
        ns_id: str = "",
    ) -> MemoryItem:
        now = time.time()
        entity_type = entity_type if entity_type in _ENTITY_TYPES else "concept"
        tags_j = json.dumps(tags or [], ensure_ascii=False)

        existing = await self._get(entity, namespace, ns_id)
        if existing:
            new_timeline = existing.timeline + [existing.compiled_truth]
            await self._db.execute(
                """UPDATE memories SET compiled_truth=?, timeline=?, tags=?, updated_at=?
                   WHERE entity=? AND namespace=? AND ns_id=?""",
                (new_info, json.dumps(new_timeline, ensure_ascii=False),
                 tags_j, now, entity, namespace, ns_id),
            )
        else:
            await self._db.execute(
                """INSERT INTO memories
                   (entity, entity_type, namespace, ns_id, compiled_truth,
                    timeline, tags, backlinks, created_at, updated_at)
                   VALUES (?,?,?,?,?, '[]',?,  '[]', ?,?)""",
                (entity, entity_type, namespace, ns_id, new_info,
                 tags_j, now, now),
            )
        await self._db.commit()
        return await self._get(entity, namespace, ns_id)

    # ──────────────────────────────────────────────
    # 创新②：三层命名空间 search（user > group > global，RRF 加权）
    # ──────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 5,
        group_id: str = "",
        user_id: str = "",
    ) -> list[MemoryItem]:
        """
        三路 FTS5 检索（global / group / user），RRF 加权融合。
        user 权重×3，group 权重×2，global 权重×1。
        """
        results: list[list[MemoryItem]] = []
        weights: list[float] = []

        # global 命名空间
        g = await self._fts_search(query, namespace="global", ns_id="", limit=top_k * 2)
        results.append(g); weights.append(_NS_WEIGHTS["global"])

        # group 命名空间
        if group_id:
            gr = await self._fts_search(query, namespace="group", ns_id=group_id, limit=top_k * 2)
            results.append(gr); weights.append(_NS_WEIGHTS["group"])

        # user 命名空间
        if user_id:
            u = await self._fts_search(query, namespace="user", ns_id=user_id, limit=top_k * 2)
            results.append(u); weights.append(_NS_WEIGHTS["user"])

        return _weighted_rrf(results, weights)[:top_k]

    async def _fts_search(
        self, query: str, namespace: str, ns_id: str, limit: int = 10
    ) -> list[MemoryItem]:
        try:
            safe = query.replace('"', '""')
            rows = await self._db.execute_fetchall(
                """SELECT m.id, m.entity, m.entity_type, m.namespace, m.ns_id,
                          m.compiled_truth, m.timeline, m.tags, m.backlinks,
                          m.created_at, m.updated_at
                   FROM memories_fts
                   JOIN memories m ON memories_fts.rowid = m.id
                   WHERE memories_fts MATCH ?
                     AND m.namespace = ? AND m.ns_id = ?
                   ORDER BY rank LIMIT ?""",
                (safe, namespace, ns_id, limit),
            )
            return [MemoryItem.from_row(r) for r in rows]
        except Exception:
            return await self._like_search(query, namespace, ns_id, limit)

    async def _like_search(
        self, query: str, namespace: str, ns_id: str, limit: int
    ) -> list[MemoryItem]:
        rows = await self._db.execute_fetchall(
            """SELECT id, entity, entity_type, namespace, ns_id, compiled_truth,
                      timeline, tags, backlinks, created_at, updated_at
               FROM memories
               WHERE (compiled_truth LIKE ? OR entity LIKE ?)
                 AND namespace=? AND ns_id=?
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", namespace, ns_id, limit),
        )
        return [MemoryItem.from_row(r) for r in rows]

    # ──────────────────────────────────────────────
    # 创新③：GM 失败归因记忆
    # ──────────────────────────────────────────────

    async def record_gm_error(
        self, command: str, error_msg: str, fix: str = "", group_id: str = ""
    ) -> None:
        """记录 GM 指令失败的原因和正确做法。"""
        entity = f"error_{command[:30]}"
        info = f"失败原因：{error_msg[:200]}"
        if fix:
            info += f"；正确做法：{fix[:200]}"
        await self.upsert(
            entity=entity,
            new_info=info,
            entity_type="error_pattern",
            tags=["gm_error", command.split("_")[0]],
            namespace="group" if group_id else "global",
            ns_id=group_id,
        )

    # ──────────────────────────────────────────────
    # 对话摘要接入
    # ──────────────────────────────────────────────

    async def ingest_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        group_id: str = "",
        user_id: str = "",
    ) -> None:
        if len(user_msg) + len(assistant_msg) < 30:
            return

        # 检测 GM 失败模式，写入 error_pattern
        if _is_gm_failure(user_msg, assistant_msg):
            cmd = _extract_gm_command(user_msg)
            err = _extract_error_msg(assistant_msg)
            fix = _extract_fix(assistant_msg)
            if cmd and err:
                await self.record_gm_error(cmd, err, fix, group_id)
                return

        # 普通对话摘要
        key = f"turn_{int(time.time())}"
        snippet = f"用户：{user_msg[:100]}；助手：{assistant_msg[:200]}"
        ns = "group" if group_id else "global"
        await self.upsert(entity=key, new_info=snippet, entity_type="concept",
                          namespace=ns, ns_id=group_id)

    # ──────────────────────────────────────────────
    # 生命周期钩子
    # ──────────────────────────────────────────────

    async def on_session_start(self, chat_id: str) -> None: pass
    async def on_session_end(self, chat_id: str) -> None: pass
    async def on_pre_compress(self, chat_id: str) -> None: pass

    # ──────────────────────────────────────────────
    # 内部查询
    # ──────────────────────────────────────────────

    async def _get(self, entity: str, namespace: str, ns_id: str) -> MemoryItem | None:
        rows = await self._db.execute_fetchall(
            """SELECT id, entity, entity_type, namespace, ns_id, compiled_truth,
                      timeline, tags, backlinks, created_at, updated_at
               FROM memories WHERE entity=? AND namespace=? AND ns_id=?""",
            (entity, namespace, ns_id),
        )
        return MemoryItem.from_row(rows[0]) if rows else None

    async def get_all(self, limit: int = 100) -> list[MemoryItem]:
        rows = await self._db.execute_fetchall(
            """SELECT id, entity, entity_type, namespace, ns_id, compiled_truth,
                      timeline, tags, backlinks, created_at, updated_at
               FROM memories ORDER BY updated_at DESC LIMIT ?""", (limit,),
        )
        return [MemoryItem.from_row(r) for r in rows]

    async def delete(self, memory_id: int) -> None:
        await self._db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        await self._db.commit()

    async def get_stale(self, stale_days: int = 30) -> list[MemoryItem]:
        threshold = time.time() - stale_days * 86400
        rows = await self._db.execute_fetchall(
            """SELECT id, entity, entity_type, namespace, ns_id, compiled_truth,
                      timeline, tags, backlinks, created_at, updated_at
               FROM memories WHERE updated_at < ? ORDER BY updated_at ASC LIMIT 100""",
            (threshold,),
        )
        return [MemoryItem.from_row(r) for r in rows]

    async def archive(self, memory_id: int) -> None:
        """将记忆标记为已归档（添加 archived tag，不删除）。"""
        rows = await self._db.execute_fetchall(
            "SELECT tags FROM memories WHERE id=?", (memory_id,)
        )
        if not rows:
            return
        tags = json.loads(rows[0][0] or "[]")
        if "archived" not in tags:
            tags.append("archived")
            await self._db.execute(
                "UPDATE memories SET tags=?, updated_at=? WHERE id=?",
                (json.dumps(tags, ensure_ascii=False), time.time(), memory_id),
            )
            await self._db.commit()

    async def get_duplicates(self, similarity_threshold: int = 80) -> list[tuple[MemoryItem, MemoryItem]]:
        """查找可能重复的记忆（按实体名前缀匹配）。"""
        all_items = await self.get_all(limit=500)
        seen: dict[str, MemoryItem] = {}
        pairs: list[tuple[MemoryItem, MemoryItem]] = []
        for item in all_items:
            key = item.entity[:15].lower()
            if key in seen:
                pairs.append((seen[key], item))
            else:
                seen[key] = item
        return pairs


# ──────────────────────────────────────────────────────────
# RRF 加权融合
# ──────────────────────────────────────────────────────────

def _weighted_rrf(
    result_lists: list[list[MemoryItem]],
    weights: list[float],
    k: int = 60,
) -> list[MemoryItem]:
    scores: dict[tuple, float] = {}
    items_by_key: dict[tuple, MemoryItem] = {}

    for result_list, weight in zip(result_lists, weights):
        for rank, item in enumerate(result_list):
            key = (item.entity, item.namespace, item.ns_id)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            items_by_key[key] = item

    sorted_keys = sorted(scores, key=lambda k_: scores[k_], reverse=True)
    result = []
    for key in sorted_keys:
        item = items_by_key[key]
        item.score = scores[key]
        result.append(item)
    return result


# ──────────────────────────────────────────────────────────
# GM 失败检测启发式
# ──────────────────────────────────────────────────────────

_GM_KEYWORDS = re.compile(r'gm|指令|send_gm|game_tool|区服|zone', re.I)
_ERROR_KEYWORDS = re.compile(r'错误|失败|error|failed|exception|not found|无效', re.I)
_FIX_PATTERN = re.compile(r'正确|应该|建议|fix|should|try', re.I)


def _is_gm_failure(user_msg: str, assistant_msg: str) -> bool:
    return bool(_GM_KEYWORDS.search(user_msg) and _ERROR_KEYWORDS.search(assistant_msg))


def _extract_gm_command(text: str) -> str:
    m = re.search(r'(send_gm_command|game_\w+|gm_\w+|\w+指令)', text, re.I)
    return m.group(1) if m else text[:20]


def _extract_error_msg(text: str) -> str:
    for line in text.splitlines():
        if _ERROR_KEYWORDS.search(line):
            return line.strip()[:200]
    return text[:100]


def _extract_fix(text: str) -> str:
    for line in text.splitlines():
        if _FIX_PATTERN.search(line):
            return line.strip()[:200]
    return ""
