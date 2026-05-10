"""
GBrain-style Dream 周期 v3.0
夜间维护任务：语义合并 · 归档过期 · 模式提取 · 技能更新 · 跨群联合学习

触发方式：
  1. 主动：asyncio cron，每天凌晨 3 点执行
  2. 被动：长时间无活动时触发（同 Curator）

7 个阶段（GBrain Dream + 跨群联合学习）：
  1. lint_memory       检查重复记忆
  2. archive_stale     归档超时未更新的记忆（30天）
  3. synthesize        LLM 合并相似记忆
  4. extract_patterns  从近期对话提取新知识
  5. update_skills     统计 SKILL.md 健康度
  6. audit_orphans     清理孤立对话摘要记忆
  7. federated_learn   跨群聚合学习：从高频 combo 提炼通用技能
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic
    from storage.memory_store import HybridMemoryStore
    from storage.group_heatmap import GroupHeatmap
    from skills.skillify import Skillify

logger = logging.getLogger("skills.dream")

_DREAM_STATE_FILE = Path.home() / ".claude/skills/.dream_state.json"
_LAST_RUN_INTERVAL = 20 * 3600   # 至少间隔 20 小时再触发
_IDLE_THRESHOLD = 4 * 3600       # 4 小时无活动才允许 Dream
_STALE_DAYS = 30                  # 超过 30 天未更新视为过期
_ARCHIVE_DAYS = 90                # 超过 90 天强制归档

class DreamCycle:
    """
    GBrain Dream 周期：夜间维护，7 个阶段。
    失败不影响主流程，所有阶段都包裹在 try/except 中。
    """

    def __init__(
        self,
        store: "HybridMemoryStore | None" = None,
        client: "anthropic.AsyncAnthropic | None" = None,
        model: str = "",
        heatmap: "GroupHeatmap | None" = None,
        skillify: "Skillify | None" = None,
    ):
        self.store = store
        self.client = client
        self._model = model or "claude-sonnet-4-6"  # fallback，正常由 main.py 从 config 传入
        self.heatmap = heatmap
        self.skillify = skillify
        self._last_run_at: float = 0.0
        self._load_state()

    def _load_state(self) -> None:
        import json
        try:
            data = json.loads(_DREAM_STATE_FILE.read_text(encoding="utf-8"))
            self._last_run_at = float(data.get("last_run_at", 0))
        except Exception:
            self._last_run_at = 0.0

    def _save_state(self) -> None:
        import json
        try:
            _DREAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _DREAM_STATE_FILE.write_text(
                json.dumps({"last_run_at": self._last_run_at}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def should_run(self, last_activity_at: float) -> bool:
        now = time.time()
        idle_enough = (now - last_activity_at) >= _IDLE_THRESHOLD
        interval_ok = (now - self._last_run_at) >= _LAST_RUN_INTERVAL
        return idle_enough and interval_ok

    async def run(self, last_activity_at: float) -> dict:
        if not self.should_run(last_activity_at):
            return {"skipped": True}

        logger.info("Dream 周期开始")
        t0 = time.monotonic()
        report: dict = {"phases": {}}

        phases = [
            ("lint_memory", self._phase_lint),
            ("archive_stale", self._phase_archive_stale),
            ("synthesize", self._phase_synthesize),
            ("extract_patterns", self._phase_extract_patterns),
            ("update_skills", self._phase_update_skills),
            ("audit_orphans", self._phase_audit_orphans),
            ("federated_learn", self._phase_federated_learn),
        ]

        for phase_name, phase_fn in phases:
            try:
                result = await phase_fn()
                report["phases"][phase_name] = result
                logger.debug("Dream.%s OK: %s", phase_name, result)
            except Exception as e:
                report["phases"][phase_name] = {"error": str(e)}
                logger.warning("Dream.%s 失败（不影响其他阶段）: %s", phase_name, e)

        self._last_run_at = time.time()
        self._save_state()

        elapsed = int((time.monotonic() - t0) * 1000)
        report["elapsed_ms"] = elapsed
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("Dream 周期完成 (%dms). report=%s", elapsed, report["phases"])
        return report

    # ──────────────────────────────────────────────
    # 7 个阶段实现
    # ──────────────────────────────────────────────

    async def _phase_lint(self) -> dict:
        """阶段 1：检查重复/空记忆。"""
        if not self.store:
            return {"skipped": "no store"}
        pairs = await self.store.get_duplicates()
        return {"duplicate_pairs": len(pairs)}

    async def _phase_archive_stale(self) -> dict:
        """阶段 2：归档超时未更新的记忆。"""
        if not self.store:
            return {"skipped": "no store"}
        stale = await self.store.get_stale(stale_days=_STALE_DAYS)
        archived = 0
        for item in stale:
            if "archived" not in item.tags:
                await self.store.archive(item.id)
                archived += 1
        return {"archived": archived, "stale_found": len(stale)}

    async def _phase_synthesize(self) -> dict:
        """阶段 3：LLM 合并相似记忆。"""
        if not self.store or not self.client:
            return {"skipped": "no store or client"}

        pairs = await self.store.get_duplicates()
        merged = 0
        for item_a, item_b in pairs[:5]:
            try:
                resp = await self.client.messages.create(
                    model=self._model,
                    max_tokens=200,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"以下两条记忆是否描述同一件事？如果是，请合并为一句话；如果不是，只回答 NO。\n"
                            f"A: {item_a.compiled_truth}\n"
                            f"B: {item_b.compiled_truth}"
                        ),
                    }],
                )
                answer = resp.content[0].text.strip() if resp.content else "NO"
                if answer.upper() != "NO" and len(answer) > 5:
                    await self.store.upsert(
                        entity=item_a.entity,
                        new_info=answer,
                        entity_type=item_a.entity_type,
                        namespace=item_a.namespace,
                        ns_id=item_a.ns_id,
                    )
                    await self.store.delete(item_b.id)
                    merged += 1
            except Exception:
                pass
        return {"merged": merged, "checked": len(pairs[:5])}

    async def _phase_extract_patterns(self) -> dict:
        """阶段 4：从近期对话摘要提取新模式（仅统计，实际提取由 Skillify 负责）。"""
        if not self.store:
            return {"skipped": "no store"}
        recent = await self.store.get_all(limit=20)
        concept_count = sum(1 for i in recent if i.entity_type == "concept")
        error_count = sum(1 for i in recent if i.entity_type == "error_pattern")
        return {"recent_concepts": concept_count, "error_patterns": error_count}

    async def _phase_update_skills(self) -> dict:
        """阶段 5：检查 SKILL.md 文件大小，输出统计。"""
        skills_dir = Path.home() / ".claude/skills"
        skill_files = list(skills_dir.rglob("SKILL.md")) if skills_dir.exists() else []
        total_size = sum(f.stat().st_size for f in skill_files)
        return {
            "skill_files": len(skill_files),
            "total_bytes": total_size,
        }

    async def _phase_audit_orphans(self) -> dict:
        """阶段 6：统计可能的孤立记忆（turn_xxx 格式，已归档的摘要）。"""
        if not self.store:
            return {"skipped": "no store"}
        all_items = await self.store.get_all(limit=200)
        orphans = [i for i in all_items if i.entity.startswith("turn_")]
        archived = [i for i in orphans if "archived" in i.tags]
        return {"orphan_turns": len(orphans), "archived": len(archived)}

    async def _phase_federated_learn(self) -> dict:
        """
        阶段 7：跨群聚合学习。
        从 GroupHeatmap 中提取在多个群高频出现的工具 combo，
        调用 Skillify 生成通用操作手册，写入 global-cross-group/SKILL.md。
        """
        if not self.heatmap or not self.skillify:
            return {"skipped": "no heatmap or skillify"}

        # 查询达到跨群阈值的 combo（≥3群，≥10次）
        combos = await self.heatmap.get_cross_group_combos(
            min_groups=3, min_total=10, limit=10
        )
        if not combos:
            return {"skipped": "no qualifying combos", "checked": 0}

        learned = 0
        for combo in combos:
            try:
                await self.skillify.skillify_cross_group(
                    combo_key=combo.combo_key,
                    tool_names=combo.tool_names,
                    group_count=combo.group_count,
                    total_count=combo.total_count,
                )
                await self.heatmap.mark_learned(combo.combo_key)
                learned += 1
                logger.info(
                    "Dream.federated_learn: %s（%d群，%d次）→ 已学习",
                    combo.combo_key, combo.group_count, combo.total_count,
                )
            except Exception as e:
                logger.warning("Dream.federated_learn combo=%s 失败: %s", combo.combo_key, e)

        # 顺便清理过期热力图记录
        cleaned = await self.heatmap.cleanup_stale()

        stats = await self.heatmap.get_stats()
        return {
            "combos_found": len(combos),
            "learned": learned,
            "heatmap_cleaned": cleaned,
            **stats,
        }
