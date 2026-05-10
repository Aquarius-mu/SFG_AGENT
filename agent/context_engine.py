"""
Hermes-style 轨迹压缩引擎 v2.0
- token 计数触发（75% context window）替代消息条数
- Haiku LLM 摘要压缩替代 head+tail 占位
- Anti-Thrashing：连续压缩节省 <10% 则暂停
- Tool Pair 完整性检查（压缩前修复孤立 tool_call/result）
- 首尾保护（protect_first=3, protect_last=6）
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger("agent.context")

_THRESHOLD_PERCENT = 0.75
_PROTECT_FIRST = 3
_PROTECT_LAST = 6
_TOOL_OUTPUT_MAX_CHARS = 500
_MIN_SAVINGS_RATIO = 0.10  # Anti-Thrashing：至少节省 10% 才值得压缩
_FALLBACK_MAX_MESSAGES = 40  # token 未知时的消息数兜底

_SUMMARY_HEADER = (
    "[已压缩对话摘要 - 以下任务已完成，请勿重复执行]\n"
    "[COMPRESSED HISTORY - Tasks below are DONE, do NOT re-execute them]\n"
)


class ContextEngine:
    def __init__(self):
        self._prompt_tokens: int = 0
        self._last_compressed_tokens: int = 0
        self._compress_paused: bool = False

    # ──────────────────────────────────────────────
    # token 追踪（每次 API 响应后调用）
    # ──────────────────────────────────────────────

    def update_from_response(self, usage) -> None:
        """Hermes: 从 API usage 更新 prompt token 计数"""
        if hasattr(usage, "input_tokens"):
            self._prompt_tokens = usage.input_tokens

    # ──────────────────────────────────────────────
    # 触发判断
    # ──────────────────────────────────────────────

    def should_compress(self, messages: list[dict], token_budget: int = 0) -> bool:
        if self._compress_paused:
            return False

        # 创新①：操作安全窗口——GM 指令执行中禁止压缩
        if self._is_in_operation_window(messages):
            logger.debug("操作安全窗口：跳过压缩（有未完成工具调用）")
            return False

        if token_budget and self._prompt_tokens:
            return self._prompt_tokens > token_budget * _THRESHOLD_PERCENT

        # 兜底：消息条数
        return len(messages) > _FALLBACK_MAX_MESSAGES

    def _is_in_operation_window(self, messages: list[dict]) -> bool:
        """
        检测最近 _PROTECT_LAST 条中是否有未配对的 tool_use（进行中的工具链）。
        有 → 禁止压缩，避免摘要掉进行中的 GM 指令上下文。
        """
        recent = messages[-_PROTECT_LAST:]
        use_ids: set[str] = set()
        result_ids: set[str] = set()
        for msg in recent:
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    use_ids.add(block.get("id", ""))
                elif block.get("type") == "tool_result":
                    result_ids.add(block.get("tool_use_id", ""))
        return bool(use_ids - result_ids)

    # ──────────────────────────────────────────────
    # 主压缩入口（async，需要 Haiku LLM）
    # ──────────────────────────────────────────────

    async def compress(
        self,
        messages: list[dict[str, Any]],
        client: "anthropic.AsyncAnthropic",
        model: str = "",
        focus_topic: str = "",
    ) -> list[dict[str, Any]]:
        """
        1. 修复孤立 tool pair
        2. 裁剪大型工具输出
        3. 保留首尾，LLM 摘要中间部分
        4. Anti-Thrashing 检测
        """
        tokens_before = self._prompt_tokens

        # Step 1: 修复 tool pair 完整性
        messages = _repair_tool_pairs(messages)

        # Step 2: 裁剪大型工具输出
        pruned = _prune_tool_outputs(messages, _TOOL_OUTPUT_MAX_CHARS)

        # Step 3: 分割首尾
        head = pruned[:_PROTECT_FIRST]
        tail = pruned[-_PROTECT_LAST:]
        middle = pruned[_PROTECT_FIRST : len(pruned) - _PROTECT_LAST]

        if not middle:
            logger.info("轨迹压缩：中间段为空，跳过")
            return pruned

        # Step 4: Haiku 生成摘要
        summary_text = await _llm_summarize(middle, client, model, focus_topic)
        summary_msg: dict[str, Any] = {
            "role": "user",
            "content": _SUMMARY_HEADER + summary_text,
        }
        compressed = head + [summary_msg] + tail

        # Step 5: Anti-Thrashing
        saved_ratio = 1.0 - len(compressed) / max(len(messages), 1)
        if tokens_before and saved_ratio < _MIN_SAVINGS_RATIO:
            logger.warning("轨迹压缩节省率 %.1f%% < 10%%，暂停后续压缩", saved_ratio * 100)
            self._compress_paused = True
        else:
            self._compress_paused = False
            self._last_compressed_tokens = tokens_before

        logger.info(
            "轨迹压缩：%d → %d 条（节省 %.1f%%）",
            len(messages), len(compressed), saved_ratio * 100,
        )
        return compressed

    def reset_pause(self) -> None:
        """会话清除后重置 Anti-Thrashing 状态"""
        self._compress_paused = False
        self._prompt_tokens = 0
        self._last_compressed_tokens = 0


# ──────────────────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────────────────

def _repair_tool_pairs(messages: list[dict]) -> list[dict]:
    """
    Hermes Tool Pair Integrity：
    移除没有对应 tool_result 的孤立 tool_use，以及没有对应 tool_use 的孤立 tool_result。
    """
    # 收集所有 tool_use_id
    use_ids: set[str] = set()
    result_ids: set[str] = set()

    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_use":
                    use_ids.add(block.get("id", ""))
                elif block.get("type") == "tool_result":
                    result_ids.add(block.get("tool_use_id", ""))

    orphan_uses = use_ids - result_ids
    orphan_results = result_ids - use_ids

    if not orphan_uses and not orphan_results:
        return messages

    logger.warning(
        "修复孤立 tool pair：%d 个孤立 tool_use，%d 个孤立 tool_result",
        len(orphan_uses), len(orphan_results),
    )

    repaired = []
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            repaired.append(msg)
            continue
        new_content = [
            block for block in content
            if not (
                isinstance(block, dict) and (
                    (block.get("type") == "tool_use" and block.get("id") in orphan_uses)
                    or (block.get("type") == "tool_result" and block.get("tool_use_id") in orphan_results)
                )
            )
        ]
        if new_content:
            repaired.append({**msg, "content": new_content})

    return repaired


def _prune_tool_outputs(messages: list[dict], max_chars: int) -> list[dict]:
    """
    裁剪超长工具输出，替换为简洁摘要，减少 token 消耗。
    """
    result = []
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                raw = block.get("content", "")
                if isinstance(raw, str) and len(raw) > max_chars:
                    truncated = raw[:max_chars]
                    block = {**block, "content": truncated + f"\n…[截断，原长 {len(raw)} 字符]"}
                elif isinstance(raw, list):
                    # 多块内容：只保留文本块并截断
                    simplified = []
                    for sub in raw:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            text = sub.get("text", "")
                            if len(text) > max_chars:
                                text = text[:max_chars] + f"\n…[截断]"
                            simplified.append({**sub, "text": text})
                    if simplified:
                        block = {**block, "content": simplified}
            new_content.append(block)

        result.append({**msg, "content": new_content})
    return result


async def _llm_summarize(
    middle: list[dict],
    client: "anthropic.AsyncAnthropic",
    model: str,
    focus_topic: str,
) -> str:
    """使用 Haiku 生成结构化对话摘要（省成本）"""
    conversation_text = _format_for_summary(middle)
    focus_hint = f"\n重点关注：{focus_topic}" if focus_topic else ""

    prompt = (
        f"请用中文简洁总结以下对话历史，结构如下：\n"
        f"- 已完成的任务\n"
        f"- 关键发现/数据\n"
        f"- 未解决的问题\n"
        f"- 下一步计划\n"
        f"{focus_hint}\n\n"
        f"对话内容：\n{conversation_text}"
    )

    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else "[摘要生成失败]"
    except Exception as e:
        logger.warning("Haiku 摘要失败，使用消息条数摘要：%s", e)
        return f"[此处压缩了 {len(middle)} 条历史消息]"


def _format_for_summary(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content[:300]
        elif isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", "")[:200])
                    elif block.get("type") == "tool_use":
                        texts.append(f"[工具调用: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        raw = block.get("content", "")
                        if isinstance(raw, str):
                            texts.append(f"[工具结果: {raw[:100]}]")
            text = " | ".join(texts)
        else:
            text = str(content)[:200]
        lines.append(f"{role}: {text}")
    return "\n".join(lines)
