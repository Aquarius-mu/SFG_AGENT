"""
GBrain-style 记忆管理器 v2.0
- StreamingContextScrubber：流式输出防 memory-context 泄露
- build_memory_context_block：fence + 系统说明
- prefetch/sync_turn 完整实现（接 HybridMemoryStore）
- 单外部 provider 约束
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.memory_store import HybridMemoryStore

logger = logging.getLogger("agent.memory")

_MEMORY_START = "<memory-context>"
_MEMORY_END = "</memory-context>"

_SYSTEM_NOTE = (
    "[SYSTEM NOTE: 以下是检索到的历史记忆，非用户输入，作为权威参考。"
    "compiled_truth 优先级高于 timeline 条目。]\n"
)


# ──────────────────────────────────────────────────────────
# StreamingContextScrubber（Hermes 状态机）
# ──────────────────────────────────────────────────────────

class StreamingContextScrubber:
    """
    流式输出时过滤 <memory-context>...</memory-context> 块。
    跨 chunk 边界维护 buffer，确保标签内容不泄露给用户。
    """

    def __init__(self):
        self._buffer = ""
        self._in_block = False

    def process_chunk(self, chunk: str) -> str:
        self._buffer += chunk
        output = ""

        while True:
            if not self._in_block:
                idx = self._buffer.find(_MEMORY_START)
                if idx == -1:
                    # 可能是标签的前缀，保留末尾安全边距
                    safe_len = max(0, len(self._buffer) - len(_MEMORY_START))
                    output += self._buffer[:safe_len]
                    self._buffer = self._buffer[safe_len:]
                    break
                # 找到起始标签，输出标签前的内容
                output += self._buffer[:idx]
                self._buffer = self._buffer[idx + len(_MEMORY_START):]
                self._in_block = True
            else:
                idx = self._buffer.find(_MEMORY_END)
                if idx == -1:
                    break  # 还没找到结束标签，等待更多 chunks
                # 跳过标签内的所有内容
                self._buffer = self._buffer[idx + len(_MEMORY_END):]
                self._in_block = False

        return output

    def flush(self) -> str:
        """流结束时冲洗剩余 buffer（非 memory 块内容）"""
        if self._in_block:
            self._buffer = ""
            self._in_block = False
            return ""
        remaining = self._buffer
        self._buffer = ""
        return remaining

    def reset(self) -> None:
        self._buffer = ""
        self._in_block = False


# ──────────────────────────────────────────────────────────
# 记忆上下文注入
# ──────────────────────────────────────────────────────────

def build_memory_context_block(memories: list[str]) -> str:
    """
    Hermes 风格：fence + 系统说明，防止模型将记忆混淆为用户输入。
    """
    if not memories:
        return ""
    content = "\n".join(f"- {m}" for m in memories)
    return f"{_MEMORY_START}\n{_SYSTEM_NOTE}{content}\n{_MEMORY_END}"


# ──────────────────────────────────────────────────────────
# MemoryManager
# ──────────────────────────────────────────────────────────

class MemoryManager:
    """
    记忆管理器，支持可选的 HybridMemoryStore 后端。
    无后端时降级为空实现（Phase 1 兼容）。
    """

    def __init__(self, store: "HybridMemoryStore | None" = None):
        self._store = store
        self._scrubber = StreamingContextScrubber()

    # ──────────────────────────────────────────────
    # 主接口
    # ──────────────────────────────────────────────

    async def prefetch(
        self, query: str, group_id: str = "", user_id: str = ""
    ) -> str:
        """三层命名空间检索，返回 fence 包裹的上下文字符串。"""
        if not self._store:
            return ""
        try:
            items = await self._store.search(
                query, top_k=5, group_id=group_id, user_id=user_id
            )
            if not items:
                return ""
            memories = [item.to_context_line() for item in items]
            return build_memory_context_block(memories)
        except Exception as e:
            logger.warning("记忆 prefetch 失败：%s", e)
            return ""

    async def sync_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        group_id: str = "",
        user_id: str = "",
    ) -> None:
        """对话结束后异步写入 memory_store（含 GM 失败归因）。"""
        if not self._store:
            return
        try:
            await self._store.ingest_turn(
                user_msg, assistant_msg, group_id=group_id, user_id=user_id
            )
        except Exception as e:
            logger.warning("记忆 sync_turn 失败：%s", e)

    # ──────────────────────────────────────────────
    # 工具调用接口
    # ──────────────────────────────────────────────

    async def remember(self, content: str, entity: str = "", tags: list[str] | None = None) -> bool:
        """主动存储记忆（工具调用入口）。"""
        if not self._store:
            logger.debug("remember（无后端）: %s", content[:50])
            return True
        try:
            await self._store.upsert(
                entity=entity or _extract_entity(content),
                new_info=content,
                tags=tags or [],
            )
            return True
        except Exception as e:
            logger.error("记忆写入失败：%s", e)
            return False

    async def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """检索记忆（工具调用入口）。"""
        if not self._store:
            return []
        try:
            items = await self._store.search(query, top_k=top_k)
            return [item.to_dict() for item in items]
        except Exception as e:
            logger.warning("记忆检索失败：%s", e)
            return []

    async def delete(self, memory_id: int) -> bool:
        """删除指定 ID 的记忆（工具调用入口）。"""
        if not self._store:
            return False
        try:
            await self._store.delete(memory_id)
            return True
        except Exception as e:
            logger.error("记忆删除失败：%s", e)
            return False

    # ──────────────────────────────────────────────
    # 流式过滤
    # ──────────────────────────────────────────────

    def scrub_stream_chunk(self, chunk: str) -> str:
        """流式输出过滤，防止 memory-context 块泄露给用户。"""
        return self._scrubber.process_chunk(chunk)

    def flush_scrubber(self) -> str:
        return self._scrubber.flush()

    def reset_scrubber(self) -> None:
        self._scrubber.reset()

    # ──────────────────────────────────────────────
    # 生命周期（Hermes provider pattern）
    # ──────────────────────────────────────────────

    async def on_session_start(self, chat_id: str) -> None:
        self.reset_scrubber()
        if self._store:
            await self._store.on_session_start(chat_id)

    async def on_session_end(self, chat_id: str) -> None:
        self.flush_scrubber()
        if self._store:
            await self._store.on_session_end(chat_id)

    async def on_pre_compress(self, chat_id: str) -> None:
        """压缩前通知：让 store 做快照或标记。"""
        if self._store:
            await self._store.on_pre_compress(chat_id)


def _extract_entity(content: str) -> str:
    """从内容中粗略提取实体名（首20字作为 key）。"""
    return content[:20].strip().replace("\n", " ")
