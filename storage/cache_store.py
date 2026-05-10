"""
用户/群组 JSON 缓存（兼容 ~/feishu_bot/cache/）
直接复用旧 bot 的缓存文件，新 bot 写入同一份
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("storage.cache")

_CACHE_DIR = os.path.expanduser("~/sfg_agent/data/cache")


class CacheStore:
    def __init__(self, cache_dir: str = _CACHE_DIR):
        self.cache_dir = cache_dir
        self._lock = asyncio.Lock()
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}.json")

    def _load(self, name: str) -> dict:
        p = self._path(name)
        if not os.path.exists(p):
            return {}
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}

    async def _save(self, name: str, data: dict) -> None:
        p = self._path(name)
        tmp = p + ".tmp"
        async with self._lock:
            with open(tmp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)

    @staticmethod
    def _is_fresh(entry: dict, ttl_days: int) -> bool:
        ts = entry.get("updated_at")
        if not ts:
            return False
        try:
            updated = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if updated.tzinfo is not None:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.utcnow()
            return now - updated < timedelta(days=ttl_days)
        except Exception:
            return False

    # ──────────────────────────────────────────────
    # 用户缓存
    # ──────────────────────────────────────────────

    def get_user(self, open_id: str) -> Optional[dict]:
        data = self._load("users")
        entry = data.get(open_id)
        if entry and self._is_fresh(entry, ttl_days=7):
            return entry
        return None

    async def set_user(self, open_id: str, name: str, **extra) -> None:
        data = self._load("users")
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = data.get(open_id, {})
        data[open_id] = {**existing, "name": name, "updated_at": now, **extra}
        await self._save("users", data)

    def get_user_name(self, open_id: str) -> Optional[str]:
        entry = self.get_user(open_id)
        return entry["name"] if entry else None

    # ──────────────────────────────────────────────
    # 群组缓存
    # ──────────────────────────────────────────────

    def get_group(self, chat_id: str) -> Optional[dict]:
        data = self._load("groups")
        entry = data.get(chat_id)
        if entry and self._is_fresh(entry, ttl_days=30):
            return entry
        return None

    async def set_group(self, chat_id: str, name: str, **extra) -> None:
        data = self._load("groups")
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = data.get(chat_id, {})
        data[chat_id] = {**existing, "name": name, "updated_at": now, **extra}
        await self._save("groups", data)

    def get_group_name(self, chat_id: str) -> Optional[str]:
        entry = self.get_group(chat_id)
        return entry["name"] if entry else None
