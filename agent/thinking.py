"""
Adaptive Thinking 控制 v3.0
- 升级到 adaptive thinking（budget_tokens 方式已 deprecated）
- per-chat_id 配置，支持 /think off/low/medium/high/xhigh/max
- auto_adjust：多轮或多错时自动升级 effort
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("agent.thinking")

# effort 级别定义（adaptive thinking 专用）
# "off" = 完全禁用 thinking；其余对应 output_config.effort 值
LEVELS = ["off", "low", "medium", "high", "xhigh", "max"]
_LEVEL_ORDER = {v: i for i, v in enumerate(LEVELS)}

_EFFORT_DESCRIPTIONS = {
    "off":   "禁用 thinking，最快响应",
    "low":   "最小化推理，简单问答优选",
    "medium": "均衡推理，日常任务默认",
    "high":  "深度推理，复杂分析任务",
    "xhigh": "超深度推理（仅 Opus 4.7）",
    "max":   "无约束深度思考",
}


class ThinkingManager:
    def __init__(self, default_level: str = "medium"):
        self._default = default_level if default_level in LEVELS else "medium"
        self._overrides: dict[str, str] = {}

    def set_level(self, chat_id: str, level: str) -> bool:
        if level not in LEVELS:
            return False
        self._overrides[chat_id] = level
        logger.info("chat %s thinking → %s", chat_id, level)
        return True

    def get_level(self, chat_id: str) -> str:
        return self._overrides.get(chat_id, self._default)

    def get_api_kwargs(self, chat_id: str) -> dict:
        """
        返回要 merge 进 messages.create() 的 thinking 相关 kwargs。
        off  → {}（不传 thinking 参数）
        其余 → {"thinking": {"type": "adaptive"}, "output_config": {"effort": "..."}}
        """
        level = self.get_level(chat_id)
        if level == "off":
            return {}
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": level},
        }

    def get_config(self, chat_id: str) -> Optional[dict]:
        """向后兼容：返回 thinking dict 或 None（不含 output_config）。"""
        level = self.get_level(chat_id)
        if level == "off":
            return None
        return {"type": "adaptive"}

    def reset(self, chat_id: str) -> None:
        self._overrides.pop(chat_id, None)

    def describe_level(self, level: str) -> str:
        return _EFFORT_DESCRIPTIONS.get(level, level)

    def auto_adjust(self, chat_id: str, iteration_count: int, error_count: int) -> str | None:
        """
        根据工具调用轮次和错误数自动升降 thinking effort。
        用户手动设置的 level 不被覆盖（只在默认级别上自动调整）。
        返回调整后的 level（若无变化返回 None）。
        """
        current = self._overrides.get(chat_id)
        if current is not None:
            return None

        if iteration_count > 8 or error_count >= 2:
            new_level = "high"
        elif iteration_count > 4 or error_count == 1:
            new_level = "medium"
        else:
            return None

        effective = self.get_level(chat_id)
        if _LEVEL_ORDER.get(new_level, 0) > _LEVEL_ORDER.get(effective, 0):
            self._overrides[chat_id] = new_level
            logger.info(
                "auto thinking upgrade chat=%s → %s (iter=%d err=%d)",
                chat_id, new_level, iteration_count, error_count,
            )
            return new_level
        return None
