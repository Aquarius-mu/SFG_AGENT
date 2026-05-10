"""
OpenClaw-style 规则引擎 SendPolicy v2.0
- 规则列表（allow/deny），替代简单 whitelist
- 支持 chat_type / chat_id / user_id 组合匹配
- Preflight 注入检测（GM 指令参数校验）
- 默认 deny（比白名单更安全）
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger("handlers.send_policy")

Action = Literal["allow", "deny"]
ChatType = Literal["group", "dm", "channel", "*"]


@dataclass
class SendRule:
    action: Action
    chat_type: ChatType = "*"
    chat_id: Optional[str] = None       # 精确匹配 chat_id
    user_id: Optional[str] = None       # 精确匹配 user_id（open_id）
    role: Optional[str] = None          # 匹配角色（admin/operator/viewer）
    note: str = ""                       # 说明，不参与匹配


class SendPolicy:
    """
    规则引擎：从上到下匹配，第一条命中的规则生效。
    无规则命中 → 默认 deny。
    """

    def __init__(self, rules: list[SendRule]):
        self.rules = rules

    def is_allowed(
        self,
        chat_type: str,
        chat_id: str,
        user_id: str,
        role: str = "",
    ) -> bool:
        for rule in self.rules:
            if self._matches(rule, chat_type, chat_id, user_id, role):
                allowed = rule.action == "allow"
                logger.debug(
                    "SendPolicy %s: chat_type=%s chat_id=%s user=%s rule=%s",
                    "ALLOW" if allowed else "DENY",
                    chat_type, chat_id, user_id, rule.note or rule.action,
                )
                return allowed
        logger.debug("SendPolicy DEFAULT DENY: user=%s", user_id)
        return False

    def _matches(
        self,
        rule: SendRule,
        chat_type: str,
        chat_id: str,
        user_id: str,
        role: str,
    ) -> bool:
        if rule.chat_type != "*" and rule.chat_type != chat_type:
            return False
        if rule.chat_id is not None and rule.chat_id != chat_id:
            return False
        if rule.user_id is not None and rule.user_id != user_id:
            return False
        if rule.role is not None and rule.role != role:
            return False
        return True

    @classmethod
    def from_config(cls, config) -> "SendPolicy":
        """
        从 config.yaml 的 send_policy.rules 构建策略。
        格式：
          send_policy:
            rules:
              - action: allow
                chat_type: group
                chat_id: "oc_xxx"
              - action: allow
                chat_type: dm
                user_id: "ou_xxx"
        若无 send_policy 配置，回退到 permissions.whitelist。
        """
        # 优先使用 send_policy.rules
        send_cfg = getattr(config, "send_policy", None)
        if send_cfg and hasattr(send_cfg, "rules"):
            rules = []
            for r in send_cfg.rules:
                rules.append(SendRule(
                    action=r.get("action", "deny"),
                    chat_type=r.get("chat_type", "*"),
                    chat_id=r.get("chat_id"),
                    user_id=r.get("user_id"),
                    role=r.get("role"),
                    note=r.get("note", ""),
                ))
            return cls(rules)

        # 回退：从 whitelist 生成 allow rules
        rules = []
        whitelist = getattr(getattr(config, "permissions", None), "whitelist", [])
        for entry in whitelist:
            open_id = entry.get("open_id") if isinstance(entry, dict) else getattr(entry, "open_id", None)
            if open_id:
                rules.append(SendRule(
                    action="allow",
                    user_id=open_id,
                    note=f"whitelist: {entry.get('name', '') if isinstance(entry, dict) else getattr(entry, 'name', '')}",
                ))
        return cls(rules)


# ──────────────────────────────────────────────────────────
# Preflight 注入检测（OpenClaw 模式）
# ──────────────────────────────────────────────────────────

# 不允许出现在 GM 指令参数中的危险模式
_INJECTION_PATTERNS = [
    re.compile(r'\$[A-Z_]{2,}'),           # 未引用的环境变量 $HOME $PATH
    re.compile(r'[`$][\s\S]*\('),          # 命令替换 $(cmd) / `cmd`
    re.compile(r'&&|\|\||;'),              # Shell 链接
    re.compile(r'<\s*script', re.I),       # XSS
    re.compile(r'\.\./'),                  # 路径遍历
    re.compile(r'(?i)--[a-z]+=.*\$'),     # 参数注入
]

_MAX_PARAM_LEN = 500  # GM 指令单个参数最大长度


def preflight_check(tool_name: str, inputs: dict) -> tuple[bool, str]:
    """
    对工具输入做安全预检。
    返回 (safe, reason)。
    """
    for key, value in inputs.items():
        if not isinstance(value, str):
            continue

        # 长度检查
        if len(value) > _MAX_PARAM_LEN:
            return False, f"参数 {key!r} 过长（{len(value)} > {_MAX_PARAM_LEN}）"

        # 注入模式检查（GM 指令等敏感工具）
        if tool_name.startswith("game_") or tool_name.startswith("gm_"):
            for pattern in _INJECTION_PATTERNS:
                if pattern.search(value):
                    return False, f"参数 {key!r} 包含可疑模式：{pattern.pattern}"

    return True, ""
