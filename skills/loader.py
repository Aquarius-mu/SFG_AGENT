"""
Hermes-style Skill Loader v2.0
- YAML frontmatter 解析（platform/triggers/disabled/config）
- 平台过滤：只加载 platform=feishu 的技能
- ${SFG_SKILL_DIR} 模板变量替换
- config 注入：从 config.yaml 读取技能声明的配置值
- mtime 增量缓存 + 5 分钟组合缓存
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("skills.loader")

_SKILLS_DIR = os.path.expanduser("~/.claude/skills")
_CACHE_TTL = 300       # 5 分钟组合缓存
_SUMMARY_MAX = 400     # 摘要最大字符
_PLATFORM = "feishu"


class SkillLoader:
    def __init__(self, skills_dir: str = _SKILLS_DIR, config: Any = None):
        self.skills_dir = Path(skills_dir)
        self.config = config
        self._file_cache: dict[str, tuple[float, dict, str]] = {}  # path → (mtime, meta, body)
        self._combined_cache: Optional[tuple[str, float]] = None

    # ──────────────────────────────────────────────
    # 主接口
    # ──────────────────────────────────────────────

    def get_relevant_skills(self, query: str = "") -> str:
        """返回当前平台可用的所有技能摘要（含 frontmatter 过滤）。"""
        now = time.monotonic()
        if self._combined_cache and now - self._combined_cache[1] < _CACHE_TTL:
            return self._combined_cache[0]

        if not self.skills_dir.exists():
            return ""

        parts = []
        skill_dir_str = str(self.skills_dir)

        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            meta, body = self._read_skill(skill_md)
            if not body:
                continue

            # 平台过滤
            if not _should_load(meta, _PLATFORM):
                continue

            name = skill_dir.name
            # 模板变量替换
            body = _substitute_vars(body, skill_dir_str)
            # config 注入
            config_block = _build_config_block(meta, self.config)
            # 摘要
            summary = body[:_SUMMARY_MAX].replace("\n", " ").strip()
            entry = f"### {name}\n{summary}…"
            if config_block:
                entry += f"\n{config_block}"
            parts.append(entry)

        combined = "\n\n".join(parts)
        self._combined_cache = (combined, now)
        logger.debug("加载 %d 个技能（平台=%s）", len(parts), _PLATFORM)
        return combined

    def get_skill_by_trigger(self, trigger: str) -> Optional[str]:
        """按 trigger 关键词精准加载技能完整内容。"""
        if not self.skills_dir.exists():
            return None
        skill_dir_str = str(self.skills_dir)
        t = trigger.lstrip("/").lower()

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta, body = self._read_skill(skill_md)
            if not _should_load(meta, _PLATFORM):
                continue
            triggers = [x.lower() for x in meta.get("triggers", [])]
            name_slug = skill_dir.name.lower()
            if t in triggers or t == name_slug or t in name_slug:
                body = _substitute_vars(body, skill_dir_str)
                return body
        return None

    def bump_use(self, skill_name: str) -> None:
        """Hermes: 记录技能使用次数，供 Curator 生命周期管理。"""
        state_file = self.skills_dir / skill_name / ".use_count"
        try:
            count = int(state_file.read_text()) if state_file.exists() else 0
            state_file.write_text(str(count + 1))
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # 内部：文件读取 + frontmatter 解析
    # ──────────────────────────────────────────────

    def _read_skill(self, skill_md: Path) -> tuple[dict, str]:
        try:
            mtime = skill_md.stat().st_mtime
            cached = self._file_cache.get(str(skill_md))
            if cached and cached[0] == mtime:
                return cached[1], cached[2]

            content = skill_md.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)
            self._file_cache[str(skill_md)] = (mtime, meta, body)
            return meta, body
        except Exception as e:
            logger.debug("读取技能失败 %s: %s", skill_md, e)
            return {}, ""


# ──────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata, body)。"""
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    yaml_str = content[3:end].strip()
    body = content[end + 4:].lstrip()

    try:
        import yaml
        meta = yaml.safe_load(yaml_str) or {}
        return meta, body
    except Exception:
        # YAML 解析失败：手动解析简单 key: value
        meta = {}
        for line in yaml_str.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        return meta, body


def _should_load(meta: dict, platform: str) -> bool:
    """平台过滤 + disabled 检查。"""
    if meta.get("disabled", False):
        return False
    skill_platform = meta.get("platform", "")
    if skill_platform and skill_platform != platform:
        return False
    return True


def _substitute_vars(content: str, skill_dir: str, session_id: str = "") -> str:
    """替换 ${SFG_SKILL_DIR} 等模板变量。"""
    content = content.replace("${SFG_SKILL_DIR}", skill_dir)
    content = content.replace("${HERMES_SKILL_DIR}", skill_dir)  # 兼容 Hermes 格式
    if session_id:
        content = content.replace("${SESSION_ID}", session_id)
    return content


def _build_config_block(meta: dict, config: Any) -> str:
    """
    Hermes config 注入：技能声明 config 键，从全局 config 读取并注入。
    例：metadata.hermes.config: {gm_server_url: game.gm_server_url}
    """
    hermes_meta = meta.get("metadata", {})
    if isinstance(hermes_meta, dict):
        config_map = hermes_meta.get("config", {}) or hermes_meta.get("hermes", {}).get("config", {})
    else:
        config_map = {}

    if not config_map or not config:
        return ""

    lines = []
    for key, config_path in config_map.items():
        value = _resolve_config_path(config, str(config_path))
        if value is not None:
            lines.append(f"{key}: {value}")

    return f"[Skill config: {', '.join(lines)}]" if lines else ""


def _resolve_config_path(config: Any, path: str) -> Any:
    """点号路径解析：'game.gm_server_url' → config.game.gm_server_url"""
    parts = path.split(".")
    obj = config
    for part in parts:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj
