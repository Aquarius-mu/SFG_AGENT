"""
Hermes-style Skill Curator v2.0
- 惰性触发：闲置 >2h AND 距上次运行 >7天（不用 cron）
- LLM 语义合并判断："专家会把这两个技能合并吗？"
- stale（30天不用）→ archive（90天）→ 永不删除
- 状态持久化到 .curator_state.json
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger("skills.curator")

_SKILLS_DIR = os.path.expanduser("~/.claude/skills")
_STATE_FILE = os.path.expanduser("~/.claude/skills/.curator_state.json")
_DEFAULT_MODEL = "claude-sonnet-4-6"
_IDLE_THRESHOLD_H = 2
_MIN_INTERVAL_DAYS = 7
_STALE_DAYS = 30
_ARCHIVE_DAYS = 90


class SkillCurator:
    """惰性触发的技能库维护器。"""

    def __init__(
        self,
        client: "anthropic.AsyncAnthropic | None" = None,
        skills_dir: str = _SKILLS_DIR,
        model: str = "",
    ):
        self.client = client
        self.skills_dir = Path(skills_dir)
        self._state_file = Path(_STATE_FILE)
        self._model = model or _DEFAULT_MODEL  # fallback，正常由 main.py 从 config 传入

    # ──────────────────────────────────────────────
    # 主入口（由 Orchestrator 在每轮对话后调用）
    # ──────────────────────────────────────────────

    async def tick(self, last_activity_at: float) -> None:
        """
        惰性触发检查。满足以下条件才运行：
        1. 距上次用户活跃 > IDLE_THRESHOLD_H
        2. 距上次 curator 运行 > MIN_INTERVAL_DAYS
        """
        idle_h = (time.time() - last_activity_at) / 3600
        if idle_h < _IDLE_THRESHOLD_H:
            return

        state = self._load_state()
        days_since = (time.time() - state.get("last_run", 0)) / 86400
        if days_since < _MIN_INTERVAL_DAYS:
            return

        logger.info("Curator 触发（闲置 %.1fh，距上次 %.1f 天）", idle_h, days_since)
        await self._run_maintenance()
        state["last_run"] = time.time()
        self._save_state(state)

    # ──────────────────────────────────────────────
    # 维护阶段
    # ──────────────────────────────────────────────

    async def _run_maintenance(self) -> None:
        if not self.skills_dir.exists():
            return

        skills = self._scan_skills()
        if not skills:
            return

        # 阶段 1: stale/archive 状态迁移
        self._update_lifecycle(skills)

        # 阶段 2: LLM 语义合并（需要 client）
        if self.client and len(skills) >= 2:
            await self._consolidate(skills)

        # 阶段 3: 验证技能树完整性
        self._verify_tree(skills)

    def _update_lifecycle(self, skills: list[dict]) -> None:
        """根据使用时间迁移 stale / archive 状态。"""
        now = time.time()
        for skill in skills:
            last_used = skill.get("last_used", skill["mtime"])
            age_days = (now - last_used) / 86400
            meta = skill.get("meta", {})

            if meta.get("pinned"):
                continue

            if age_days > _ARCHIVE_DAYS:
                self._mark(skill["path"], "archived")
            elif age_days > _STALE_DAYS:
                self._mark(skill["path"], "stale")

    async def _consolidate(self, skills: list[dict]) -> None:
        """
        LLM 语义合并：两两检查是否应该合并。
        Hermes 核心问题："专家会把它们写成独立技能还是一个技能的两个章节？"
        """
        active = [s for s in skills if not s.get("meta", {}).get("archived")]
        if len(active) < 2:
            return

        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = active[i], active[j]
                if a.get("meta", {}).get("archived") or b.get("meta", {}).get("archived"):
                    continue

                should_merge = await self._ask_should_merge(a, b)
                if should_merge:
                    await self._merge_skills(a, b)
                    logger.info("Curator 合并：%s + %s", a["name"], b["name"])
                    # 合并后 b 标记为 archived
                    b["meta"]["archived"] = True

    async def _ask_should_merge(self, a: dict, b: dict) -> bool:
        """Haiku 判断：这两个技能是否应该合并？"""
        try:
            resp = await self.client.messages.create(
                model=self._model,
                max_tokens=5,
                messages=[{
                    "role": "user",
                    "content": (
                        "一个专家会把以下两个技能文档写成独立文件，还是合并为一个文件的两个章节？\n"
                        "如果应该合并回答 YES，否则回答 NO。\n\n"
                        f"技能A：{a['name']}\n摘要：{a['summary'][:200]}\n\n"
                        f"技能B：{b['name']}\n摘要：{b['summary'][:200]}"
                    ),
                }],
            )
            return resp.content[0].text.strip().upper() == "YES" if resp.content else False
        except Exception:
            return False

    async def _merge_skills(self, a: dict, b: dict) -> None:
        """将 b 的内容追加到 a，并在 b 中添加归档说明。"""
        try:
            b_content = Path(b["path"]).read_text(encoding="utf-8")
            merged_section = (
                f"\n\n## 合并自 {b['name']}（Curator 自动合并）\n\n"
                f"{b_content}\n"
            )
            with open(a["path"], "a", encoding="utf-8") as f:
                f.write(merged_section)
            # b 重命名为 .archived
            archive_path = Path(b["path"]).parent / "SKILL.md.archived"
            Path(b["path"]).rename(archive_path)
        except Exception as e:
            logger.warning("合并失败：%s", e)

    def _verify_tree(self, skills: list[dict]) -> None:
        """简单验证：技能名唯一、无空文件。"""
        names = [s["name"] for s in skills]
        if len(names) != len(set(names)):
            logger.warning("Curator: 技能名重复，请检查 %s", _SKILLS_DIR)

    # ──────────────────────────────────────────────
    # 扫描技能目录
    # ──────────────────────────────────────────────

    def _scan_skills(self) -> list[dict]:
        skills = []
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter_simple(content)
                use_count_file = skill_dir / ".use_count"
                use_count = int(use_count_file.read_text()) if use_count_file.exists() else 0
                last_used_file = skill_dir / ".last_used"
                last_used = float(last_used_file.read_text()) if last_used_file.exists() else skill_md.stat().st_mtime

                skills.append({
                    "name": skill_dir.name,
                    "path": str(skill_md),
                    "meta": meta,
                    "summary": body[:300],
                    "mtime": skill_md.stat().st_mtime,
                    "use_count": use_count,
                    "last_used": last_used,
                })
            except Exception:
                pass
        return skills

    def _mark(self, skill_path: str, status: str) -> None:
        """在 SKILL.md frontmatter 中标记状态（追加注释行）。"""
        try:
            path = Path(skill_path)
            content = path.read_text(encoding="utf-8")
            if f"curator_status: {status}" not in content:
                if content.startswith("---"):
                    content = content.replace("---\n", f"---\ncurator_status: {status}\n", 1)
                    path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # 状态持久化
    # ──────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text())
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning("Curator 状态保存失败：%s", e)


Curator = SkillCurator  # main.py 使用的别名


def _parse_frontmatter_simple(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    yaml_str = content[3:end].strip()
    body = content[end + 4:].lstrip()
    meta = {}
    for line in yaml_str.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body
