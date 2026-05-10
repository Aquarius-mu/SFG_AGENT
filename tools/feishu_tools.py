"""
飞书工具集（Phase 2）
lark-cli 包装：搜群/查用户/发消息/读取多维表格
"""

PROVIDER_META: dict = {
    "name": "feishu_tools",
    "description": "飞书消息/群/用户/文档工具",
    "register_fn": "register_feishu_tools",
    "domain_name": "feishu",
    "domain_keywords": [
        "飞书", "发消息", "发通知", "群公告", "文档", "@", "at",
        "消息", "通知", "知识库", "wiki", "多维表格", "lark", "卡片",
        "群聊", "搜群", "查用户",
    ],
    "tool_name_prefixes": ["feishu_", "send_message", "search_group", "get_user_info"],
    "weight": 2,
    "requires_config_key": None,
}

SETUP_META: dict = {
    "step_name": "飞书 Bot 登录",
    "description": "lark-cli auth login --as bot，登录飞书 Bot 账号",
    "optional": True,
    "setup_fn": "setup_step",
}


def setup_step() -> bool:
    """飞书 Bot 登录（调用 lark-cli auth login）。"""
    import shutil, subprocess

    lark = None
    from pathlib import Path as _Path
    HOME = _Path.home()
    for candidate in [
        shutil.which("lark-cli"),
        str(HOME / "nodejs/node22.15/bin/lark-cli"),
        str(HOME / "nodejs/node20/bin/lark-cli"),
        str(HOME / ".npm-global/bin/lark-cli"),
        str(HOME / ".local/bin/lark-cli"),
    ]:
        if candidate and _Path(candidate).is_file():
            lark = candidate
            break

    if not lark:
        print("  ✗ 未找到 lark-cli，请先安装：npm install -g @larksuite/lark-cli")
        return False

    # 检查是否已登录
    result = subprocess.run([lark, "whoami", "--as", "bot"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        print(f"  ✓ Bot 已登录：{result.stdout.strip()[:80]}")
        ans = input("  重新登录？[y/N]: ").strip().lower()
        if ans != "y":
            print("  ↷ 跳过：保留现有登录状态")
            return True

    print("  · 正在启动飞书 Bot 登录流程…")
    result = subprocess.run([lark, "auth", "login", "--as", "bot"])
    if result.returncode == 0:
        print("  ✓ 飞书 Bot 登录成功")
        return True
    else:
        print("  ⚠ 登录可能未完成，请手动运行：lark-cli auth login --as bot")
        return False
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger("tools.feishu")

_CACHE_DIR = os.path.expanduser("~/feishu_bot/cache")


def _lark(args: list[str], as_: str = "user", timeout: int = 20) -> dict:
    """调用 lark-cli 并解析 JSON 返回。"""
    cmd = ["lark-cli"] + args + ["--as", as_]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        raw = result.stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            stderr = result.stderr.decode("utf-8", errors="replace")
            return {"error": f"lark-cli 无输出. stderr: {stderr[:200]}"}
        return json.loads(raw)
    except subprocess.TimeoutExpired:
        return {"error": "lark-cli 超时"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失败: {e}", "raw": raw[:300]}
    except Exception as e:
        return {"error": str(e)}


def _read_cache(key: str) -> str | None:
    path = os.path.join(_CACHE_DIR, f"{key}.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _write_cache(key: str, value: str) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f"{key}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)
    except Exception:
        pass


def register_feishu_tools(registry: "ToolRegistry") -> None:
    """注册所有飞书工具到 registry。"""

    @registry.register(
        name="search_feishu_group",
        description="搜索飞书群组，返回群名、chat_id 列表。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "群名关键词"},
                "limit": {"type": "integer", "description": "最多返回条数（默认 10）", "default": 10},
            },
            "required": ["query"],
        },
        is_deterministic=True,
    )
    async def search_feishu_group(query: str, limit: int = 10) -> dict:
        resp = _lark(["im", "+chat-search", "--query", query])
        if "error" in resp:
            return resp
        items = resp.get("data", {}).get("items", []) or []
        results = []
        for item in items[:limit]:
            chat_id = item.get("chat_id", "")
            name = item.get("name", "")
            if chat_id and name:
                _write_cache(f"chat_{name}", chat_id)
            results.append({"chat_id": chat_id, "name": name})
        return {"groups": results, "total": len(results)}

    @registry.register(
        name="get_user_info",
        description="根据 open_id 查询飞书用户的姓名、部门、邮箱。",
        input_schema={
            "type": "object",
            "properties": {
                "open_id": {"type": "string", "description": "用户 open_id（ou_ 开头）"},
            },
            "required": ["open_id"],
        },
        is_deterministic=True,
    )
    async def get_user_info(open_id: str) -> dict:
        resp = _lark([
            "contact", "+search-user", "--query", open_id,
        ])
        if "error" in resp:
            return resp
        users = resp.get("data", {}).get("users", []) or []
        for u in users:
            if u.get("open_id") == open_id:
                return {
                    "open_id": open_id,
                    "name": u.get("name", ""),
                    "department": u.get("department", ""),
                    "email": u.get("email", ""),
                }
        return {"open_id": open_id, "error": "用户未找到"}

    @registry.register(
        name="search_feishu_user",
        description="按姓名或工号搜索飞书用户，返回 open_id 列表。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "姓名/工号/邮箱关键词"},
                "limit": {"type": "integer", "description": "最多返回条数（默认 5）", "default": 5},
            },
            "required": ["query"],
        },
        is_deterministic=True,
    )
    async def search_feishu_user(query: str, limit: int = 5) -> dict:
        resp = _lark(["contact", "+search-user", "--query", query])
        if "error" in resp:
            return resp
        users = resp.get("data", {}).get("users", []) or []
        results = [
            {
                "open_id": u.get("open_id", ""),
                "name": u.get("name", ""),
                "department": u.get("department", ""),
            }
            for u in users[:limit]
        ]
        return {"users": results, "total": len(results)}

    @registry.register(
        name="send_feishu_message",
        description="向指定飞书群或用户发送文本消息（需要 operator 及以上权限）。",
        input_schema={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "目标群 chat_id（oc_ 开头）或用户 open_id（ou_ 开头）"},
                "text": {"type": "string", "description": "消息内容（支持 Markdown）"},
            },
            "required": ["chat_id", "text"],
        },
        is_deterministic=False,
        allowed_roles=["admin", "operator"],
    )
    async def send_feishu_message(chat_id: str, text: str) -> dict:
        receive_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
        resp = _lark([
            "im", "+messages-send",
            "--receive-id-type", receive_type,
            "--receive-id", chat_id,
            "--msg-type", "text",
            "--content", json.dumps({"text": text}, ensure_ascii=False),
        ], as_="bot")
        if "error" in resp:
            return resp
        code = resp.get("code", -1)
        if code == 0:
            msg_id = resp.get("data", {}).get("message_id", "")
            return {"ok": True, "message_id": msg_id}
        return {"ok": False, "code": code, "msg": resp.get("msg", "")}

    @registry.register(
        name="get_chat_members",
        description="获取飞书群成员列表，返回 open_id 和姓名。",
        input_schema={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "群 chat_id（oc_ 开头）"},
            },
            "required": ["chat_id"],
        },
        is_deterministic=True,
    )
    async def get_chat_members(chat_id: str) -> dict:
        resp = _lark([
            "im", "chat.members", "get",
            "--params", json.dumps({"chat_id": chat_id}),
        ])
        if "error" in resp:
            return resp
        members = resp.get("data", {}).get("items", []) or []
        results = [
            {"open_id": m.get("member_id", ""), "name": m.get("name", "")}
            for m in members
            if m.get("member_id_type") == "open_id"
        ]
        return {"members": results, "total": len(results)}

    @registry.register(
        name="read_bitable_records",
        description=(
            "读取多维表格（Bitable）记录。"
            "base_token 和 table_id 可从多维表格 URL 解析：/base/{base_token}?table={table_id}。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "base_token": {"type": "string", "description": "多维表格 base token"},
                "table_id": {"type": "string", "description": "表格 table_id（tbl 开头）"},
                "limit": {"type": "integer", "description": "最多读取行数（默认 50）", "default": 50},
                "filter_field": {"type": "string", "description": "过滤字段名（可选）"},
                "filter_value": {"type": "string", "description": "过滤字段值（可选）"},
            },
            "required": ["base_token", "table_id"],
        },
        is_deterministic=True,
    )
    async def read_bitable_records(
        base_token: str,
        table_id: str,
        limit: int = 50,
        filter_field: str = "",
        filter_value: str = "",
    ) -> dict:
        args = [
            "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", str(min(limit, 200)),
        ]
        resp = _lark(args)
        if "error" in resp:
            return resp

        data = resp.get("data", {})
        fields = data.get("fields", [])
        rows = data.get("data", [])

        if filter_field and filter_value and fields:
            try:
                col_idx = fields.index(filter_field)
                rows = [r for r in rows if str(r[col_idx]) == filter_value]
            except (ValueError, IndexError):
                pass

        records = []
        for row in rows:
            record: dict = {}
            for i, field in enumerate(fields):
                record[field] = row[i] if i < len(row) else None
            records.append(record)

        return {
            "fields": fields,
            "records": records,
            "total": len(records),
            "has_more": data.get("has_more", False),
        }

    @registry.register(
        name="get_wiki_node",
        description="通过 wiki 节点 token 获取文档信息（标题、URL、类型）。",
        input_schema={
            "type": "object",
            "properties": {
                "node_token": {"type": "string", "description": "wiki 节点 token（URL 中的路径部分）"},
            },
            "required": ["node_token"],
        },
        is_deterministic=True,
    )
    async def get_wiki_node(node_token: str) -> dict:
        resp = _lark([
            "wiki", "spaces", "get_node",
            "--params", json.dumps({"token": node_token}),
        ])
        if "error" in resp:
            return resp
        node = resp.get("data", {}).get("node", {})
        return {
            "title": node.get("title", ""),
            "obj_token": node.get("obj_token", ""),
            "obj_type": node.get("obj_type", ""),
            "url": node.get("url", ""),
        }
