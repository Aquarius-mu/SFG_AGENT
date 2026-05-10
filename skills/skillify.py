"""
GBrain-style Skillify v3.0
- 单群 combo 热力图（内存）：本实例内工具组合频次统计
- 跨群 combo 热力图（GroupHeatmap SQLite）：多群聚合信号
- 双通道触发：
    1. 单群模式：combo 在本群出现 ≥3 次（快速响应）
    2. 跨群模式：combo 在 ≥3 个不同群各自出现，由 Dream 批量触发
- 追加写入 SKILL.md（去重 + 上限保护）
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import anthropic
    from storage.group_heatmap import GroupHeatmap

logger = logging.getLogger("skills.skillify")

_SKILLS_DIR = os.path.expanduser("~/.claude/skills")
_MAX_ENTRIES_PER_FILE = 20   # 单文件最多条目数
_MIN_SINGLE_COMBO = 3        # 单群触发阈值
_CROSS_GROUP_SKILL_DIR = "global-cross-group"  # 跨群学习专用目录


class Skillify:
    """对话结束后异步学习新技能模式。"""

    def __init__(
        self,
        client: "anthropic.AsyncAnthropic | None" = None,
        skills_dir: str = _SKILLS_DIR,
        model: str = "",
        heatmap: "GroupHeatmap | None" = None,
    ):
        self.client = client
        self.skills_dir = Path(skills_dir)
        self._model = model or "claude-sonnet-4-6"
        self.heatmap = heatmap

        # 单群内存热力图（per-combo 计数）
        self._combo_counter: dict[frozenset, int] = {}
        self._learned_combos: set[frozenset] = set()

    def record_tool_combo(self, tool_names: list[str], group_id: str = "") -> bool:
        """
        记录本轮工具组合。
        - 写入跨群 GroupHeatmap（异步，由调用方负责 await）
        - 返回是否触发单群 Skillify（本群 combo ≥ _MIN_SINGLE_COMBO 次）
        """
        if not tool_names:
            return False
        combo = frozenset(tool_names)
        if combo in self._learned_combos:
            return False
        self._combo_counter[combo] = self._combo_counter.get(combo, 0) + 1
        count = self._combo_counter[combo]
        if count >= _MIN_SINGLE_COMBO:
            self._learned_combos.add(combo)
            logger.info("单群热力图触发 Skillify：%s（出现%d次）", set(combo), count)
            return True
        return False

    async def record_cross_group(self, tool_names: list[str], group_id: str) -> None:
        """写入跨群 GroupHeatmap（非阻塞，失败不影响主流程）。"""
        if self.heatmap and tool_names and group_id:
            try:
                await self.heatmap.record(group_id, tool_names)
            except Exception as e:
                logger.debug("GroupHeatmap 写入失败（不影响主流程）: %s", e)

    async def maybe_skillify(
        self,
        user_msg: str,
        assistant_msg: str,
        source_label: str = "",
    ) -> None:
        """
        对话结束后触发。全程异步，失败不影响主流程。
        source_label: 用于 SKILL.md 注释（如 "跨群聚合" 或 群名）
        """
        if not self.client:
            return
        if len(user_msg) + len(assistant_msg) < 50:
            return

        try:
            exchange = {"user": user_msg[:500], "assistant": assistant_msg[:800]}

            verdict = await self._quick_judge(exchange)
            if verdict != "YES":
                return

            pattern = await self._extract_pattern(exchange)
            if not pattern:
                return

            if not await self._audit(pattern):
                logger.debug("Skillify 审计未通过，跳过写入")
                return

            target = self._pick_target(pattern, source_label=source_label)
            await self._append_to_skill(target, pattern, source_label=source_label)
            logger.info("Skillify: 新模式已写入 %s", target)

        except Exception as e:
            logger.debug("Skillify 异常（不影响主流程）：%s", e)

    async def skillify_cross_group(
        self,
        combo_key: str,
        tool_names: list[str],
        group_count: int,
        total_count: int,
    ) -> None:
        """
        Dream 阶段调用：基于跨群聚合信号提炼技能。
        不需要原始对话，直接从工具组合生成操作手册。
        """
        if not self.client:
            return
        try:
            pattern = await self._extract_cross_group_pattern(
                tool_names, group_count, total_count
            )
            if not pattern:
                return
            if not await self._audit(pattern):
                return
            target = self._pick_cross_group_target()
            await self._append_to_skill(
                target, pattern,
                source_label=f"跨群聚合（{group_count}群，{total_count}次触发）",
            )
            logger.info(
                "Skillify 跨群学习完成: combo=%s → %s", combo_key, target
            )
        except Exception as e:
            logger.debug("跨群 Skillify 异常: %s", e)

    # ──────────────────────────────────────────────
    # 内部步骤
    # ──────────────────────────────────────────────

    async def _quick_judge(self, exchange: dict) -> str:
        resp = await self.client.messages.create(
            model=self._model,
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": (
                    "以下对话是否包含值得记录的可复用操作模式（飞书操作、GM指令套路、错误处理等）？\n"
                    "只回答 YES 或 NO。\n\n"
                    f"用户：{exchange['user']}\n"
                    f"助手：{exchange['assistant']}"
                ),
            }],
        )
        return resp.content[0].text.strip().upper() if resp.content else "NO"

    async def _extract_pattern(self, exchange: dict) -> str:
        resp = await self.client.messages.create(
            model=self._model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "从以下对话中提取一个简洁的操作模式，格式：\n"
                    "标题：（10字以内）\n"
                    "场景：（适用条件）\n"
                    "步骤：（2-5步）\n"
                    "注意：（常见错误或约束）\n\n"
                    f"用户：{exchange['user']}\n"
                    f"助手：{exchange['assistant']}"
                ),
            }],
        )
        return resp.content[0].text.strip() if resp.content else ""

    async def _extract_cross_group_pattern(
        self,
        tool_names: list[str],
        group_count: int,
        total_count: int,
    ) -> str:
        """基于工具组合（无原始对话）生成操作手册。"""
        resp = await self.client.messages.create(
            model=self._model,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": (
                    f"以下工具组合在 {group_count} 个不同的飞书群中共触发了 {total_count} 次，"
                    "说明这是一个高频的通用操作流程。\n"
                    f"工具列表：{', '.join(tool_names)}\n\n"
                    "请根据工具名称推断该操作流程，并生成操作手册，格式：\n"
                    "标题：（10字以内，描述该流程的核心目标）\n"
                    "场景：（何时使用该流程）\n"
                    "步骤：（2-5步，对应上述工具的调用顺序）\n"
                    "注意：（常见错误、权限要求或数据验证要点）"
                ),
            }],
        )
        return resp.content[0].text.strip() if resp.content else ""

    async def _audit(self, pattern: str) -> bool:
        resp = await self.client.messages.create(
            model=self._model,
            max_tokens=5,
            messages=[{
                "role": "user",
                "content": (
                    "以下操作模式是否满足：具体可操作、不是常识、适合游戏运营场景？\n"
                    "只回答 YES 或 NO。\n\n"
                    f"{pattern}"
                ),
            }],
        )
        return resp.content[0].text.strip().upper() == "YES" if resp.content else False

    def _pick_target(self, pattern: str, source_label: str = "") -> str:
        p = pattern.lower()
        if any(k in p for k in ("飞书", "lark", "feishu", "卡片", "消息")):
            category = "feishu-operations"
        elif any(k in p for k in ("gm", "区服", "游戏", "指令", "角色")):
            category = "game-gm-commands"
        elif any(k in p for k in ("记忆", "记录", "搜索", "查询")):
            category = "memory-patterns"
        else:
            category = "general-patterns"

        skill_dir = self.skills_dir / category
        skill_dir.mkdir(parents=True, exist_ok=True)
        return str(skill_dir / "SKILL.md")

    def _pick_cross_group_target(self) -> str:
        skill_dir = self.skills_dir / _CROSS_GROUP_SKILL_DIR
        skill_dir.mkdir(parents=True, exist_ok=True)
        return str(skill_dir / "SKILL.md")

    async def _append_to_skill(
        self, skill_path: str, pattern: str, source_label: str = ""
    ) -> None:
        path = Path(skill_path)
        timestamp = time.strftime("%Y-%m-%d %H:%M")
        label = f" · {source_label}" if source_label else ""
        entry = f"\n\n## 自动学习 {timestamp}{label}\n\n{pattern}\n"

        if not path.exists():
            header = (
                "---\n"
                f"name: {path.parent.name}\n"
                "platform: feishu\n"
                "auto_generated: true\n"
                "---\n"
            )
            path.write_text(header + entry, encoding="utf-8")
            return

        existing = path.read_text(encoding="utf-8")

        title_line = next((l for l in pattern.splitlines() if l.startswith("标题：")), "")
        if title_line and title_line[3:].strip() in existing:
            logger.debug("Skillify: 模式已存在，跳过写入（%s）", title_line)
            return

        entry_count = existing.count("## 自动学习")
        if entry_count >= _MAX_ENTRIES_PER_FILE:
            logger.info(
                "Skillify: %s 已有 %d 条，达到上限（%d），等待 Curator 清理",
                path.name, entry_count, _MAX_ENTRIES_PER_FILE,
            )
            return

        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
