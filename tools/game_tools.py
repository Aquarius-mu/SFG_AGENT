"""
游戏服务器工具集（Phase 2）
直接调用 gm_server HTTP API（与 MCP server.py 逻辑相同，不经过 MCP 协议）
"""

PROVIDER_META: dict = {
    "name": "game_tools",
    "description": "游戏服务器 GM 工具",
    "register_fn": "register_game_tools",
    "domain_name": "game",
    "domain_keywords": [
        "gm", "区服", "玩家", "发奖", "邮件", "角色", "英雄", "副本",
        "战斗", "联盟", "服务器", "充值", "封号", "解封", "重置",
        "gm指令", "gm命令", "游戏", "zone", "zoneId", "game",
    ],
    "tool_name_prefixes": ["game_", "list_zones", "send_gm", "batch_send_gm",
                           "copy_role", "get_role", "ping_zone"],
    "weight": 3,
    "requires_config_key": "game.enabled",
}
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

# ─── Setup 向导（仅使用 stdlib，供 setup_agent.py 自动发现）─────────────────

SETUP_META: dict = {
    "step_name": "配置 GM 服务器",
    "description": "填写 GM 后台地址、Token，写入 ~/.claude/mcp-servers/gm_server/config.json",
    "optional": True,
    "setup_fn": "setup_step",
}


def setup_step() -> bool:
    """交互式配置 gm_server/config.json，仅使用 stdlib。"""
    from pathlib import Path as _Path
    import json as _json, os as _os

    HOME = _Path.home()
    gm_dir   = HOME / ".claude/mcp-servers/gm_server"
    cfg_path = gm_dir / "config.json"

    print()
    print("  GM 服务器配置存放于：~/.claude/mcp-servers/gm_server/config.json")
    print("  游戏工具通过此配置直接 HTTP 调用 GM 后台 API（不依赖 MCP）")
    print()

    if cfg_path.exists():
        ans = input("  config.json 已存在，重新配置？[y/N]: ").strip().lower()
        if ans != "y":
            print("  ↷ 跳过：保留现有 GM 配置")
            return True

    gm_dir.mkdir(parents=True, exist_ok=True)

    base_cfg: dict = {}
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                base_cfg = _json.load(f)
        except Exception:
            pass

    def _ask(prompt: str, default: str = "", secret: bool = False) -> str | None:
        disp = ("*" * min(len(default), 8) + "…") if secret and default else default
        suffix = f" ({disp})" if disp else " [s=跳过]"
        raw = input(f"  {prompt}{suffix}: ").strip()
        if raw.lower() in ("s", "skip"):
            return None
        return raw or default or None

    base_url = _ask("GM 后台地址（如 http://192.168.1.1:8080）",
                    base_cfg.get("base_url", ""))
    token    = _ask("Token（与 username/password 二选一）",
                    base_cfg.get("token", ""), secret=True)
    username = _ask("用户名（有 token 可跳过）", base_cfg.get("username", ""))
    password = _ask("密码（有 token 可跳过）",   base_cfg.get("password", ""), secret=True)
    nick     = _ask("昵称/Nick（写入 Cookie NICK）", base_cfg.get("nick", ""))
    js_path  = _ask("GM 指令目录 JS 路径（用于搜索指令，可跳过）",
                    base_cfg.get("gm_cmd_js_path", ""))

    new_cfg = {k: v for k, v in {
        "base_url":       base_url or base_cfg.get("base_url", ""),
        "token":          token    or base_cfg.get("token", ""),
        "username":       username or base_cfg.get("username", ""),
        "password":       password or base_cfg.get("password", ""),
        "nick":           nick     or base_cfg.get("nick", ""),
        "gm_cmd_js_path": js_path  or base_cfg.get("gm_cmd_js_path", ""),
        "zones":          base_cfg.get("zones", []),
    }.items() if v}

    with open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump(new_cfg, f, ensure_ascii=False, indent=2)
    _os.chmod(cfg_path, 0o600)

    # 写一份无密码的 example
    example_cfg = {k: ("***" if k in ("token", "password") else v)
                   for k, v in new_cfg.items()}
    with open(gm_dir / "config.json.example", "w", encoding="utf-8") as f:
        _json.dump(example_cfg, f, ensure_ascii=False, indent=2)

    print(f"  ✓ GM 配置已保存：{cfg_path}（权限 600）")
    return True

logger = logging.getLogger("tools.game")

# httpx 在函数内部懒加载，避免 setup 阶段未安装时报错

_CONFIG_PATH = Path.home() / ".claude/mcp-servers/gm_server/config.json"
_AUDIT_LOG = Path.home() / "sfg_agent/data/audit.log"

_cmd_catalog: dict[str, str] = {}
_cmd_catalog_loaded = False


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise RuntimeError(
            f"gm_server 配置文件不存在: {_CONFIG_PATH}\n"
            "请复制 config.json.example 并填写 base_url / token 等配置。"
        )
    size = _CONFIG_PATH.stat().st_size
    if size > 10_000:
        raise RuntimeError(
            f"gm_server 配置文件异常（{size} 字节），可能被游戏日志覆盖。\n"
            f"请重新复制 config.json.example → config.json 并填写配置。"
        )
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"gm_server 配置文件 JSON 解析失败: {e}")


def _build_auth_data(cfg: dict) -> dict:
    params: dict = {}
    if cfg.get("token"):
        params["token"] = cfg["token"]
    if cfg.get("username"):
        params["user"] = cfg["username"]
    if cfg.get("password"):
        params["password"] = cfg["password"]
    return params


def _build_auth_query(cfg: dict) -> dict:
    if cfg.get("token"):
        return {"token": cfg["token"]}
    return {}


def _build_cookies(cfg: dict) -> dict:
    cookies: dict = {}
    if cfg.get("token"):
        cookies["X_MOA_TOKEN"] = cfg["token"]
    nick = cfg.get("nick") or cfg.get("username", "")
    if nick:
        cookies["NICK"] = nick
    return cookies


def _audit(action: str, zone_id: int | str, command: str, result: str, role: str = "") -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] role={role} action={action} zone={zone_id} cmd={command!r} result={result[:200]!r}\n"
    try:
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _load_cmd_catalog(js_path: str) -> None:
    global _cmd_catalog, _cmd_catalog_loaded
    if _cmd_catalog_loaded:
        return
    _cmd_catalog_loaded = True
    path = Path(js_path)
    if not path.exists():
        return
    pattern = re.compile(r'"([a-zA-Z0-9_]+)\s+#([^"]+)"')
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                _cmd_catalog[m.group(1).strip()] = m.group(2).strip()


def register_game_tools(registry: "ToolRegistry") -> None:
    """注册所有游戏工具到 registry。"""

    @registry.register(
        name="list_zones",
        description=(
            "查询游戏区服列表。优先从 GM 后台 API 动态获取，失败则读取配置中的静态列表。"
            "可按区服名称或 ID 关键词过滤。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "按区服名称或 ID 关键词过滤（可选，不填返回全部）",
                },
            },
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator", "viewer"],
    )
    async def list_zones(name_filter: str = "") -> dict:
        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        import httpx
        zones: list[dict] = []
        try:
            base_url = cfg["base_url"].rstrip("/")
            url = f"{base_url}/api.php"
            query = {"mod": "GmTools", "act": "zonelist", **_build_auth_query(cfg)}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=query, cookies=_build_cookies(cfg))
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for z in data:
                            zones.append({
                                "id": z.get("id", z.get("iZoneId", 0)),
                                "name": str(z.get("name", z.get("sZoneName", ""))),
                                "merge_to": z.get("merge_to_zone", z.get("iMergeToZone", 0)),
                            })
                    elif isinstance(data, dict) and data.get("data"):
                        for z in data["data"]:
                            zones.append({
                                "id": z.get("id", 0),
                                "name": str(z.get("name", "")),
                                "merge_to": z.get("merge_to_zone", 0),
                            })
        except Exception as e:
            logger.debug("动态获取区服列表失败，降级到静态配置: %s", e)

        if not zones and cfg.get("zones"):
            zones = list(cfg["zones"])

        if not zones:
            return {"error": "未找到区服列表，请检查 GM 后台连通性或 config.json 的 zones 配置"}

        if name_filter:
            lf = name_filter.lower()
            zones = [z for z in zones if lf in str(z["id"]) or lf in z["name"].lower()]

        return {"zones": zones, "total": len(zones)}

    @registry.register(
        name="search_gm_commands",
        description=(
            "在 GM 指令目录中搜索，支持英文指令名或中文说明关键词，最多返回 20 条。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["keyword"],
        },
        is_deterministic=True,
        allowed_roles=["admin", "operator", "viewer"],
    )
    async def search_gm_commands(keyword: str) -> dict:
        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        js_path = cfg.get("gm_cmd_js_path", "")
        if js_path:
            _load_cmd_catalog(js_path)

        if not _cmd_catalog:
            return {"error": "指令目录未加载，请检查 config.json 中的 gm_cmd_js_path"}

        kw = keyword.lower()
        results = []
        for cmd_name, desc in _cmd_catalog.items():
            if kw in cmd_name.lower() or kw in desc.lower():
                results.append({"command": cmd_name, "description": desc})
            if len(results) >= 20:
                break

        return {"commands": results, "total": len(results)}

    @registry.register(
        name="send_gm_command",
        description=(
            "向指定区服发送 GM 指令并返回结果。"
            "示例：send_gm_command(zone_id=20, command='see 123456')"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "integer", "description": "目标区服 ID"},
                "command": {"type": "string", "description": "GM 指令，多条用换行分隔"},
                "merge_to": {
                    "type": "integer",
                    "description": "合区目标 zone_id，未合区填 0（默认 0）",
                    "default": 0,
                },
            },
            "required": ["zone_id", "command"],
        },
        is_deterministic=False,
        allowed_roles=["admin", "operator"],
    )
    async def send_gm_command(zone_id: int, command: str, merge_to: int = 0) -> dict:
        import httpx
        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        base_url = cfg["base_url"].rstrip("/")
        url = f"{base_url}/api.php"

        data = {
            "zoneid": str(zone_id),
            "mergeto": str(merge_to),
            "cmd": command,
            "explaints": "0",
            **_build_auth_data(cfg),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    params={"mod": "GmTools", "act": "operateCmd"},
                    data=data,
                    cookies=_build_cookies(cfg),
                )
                resp.raise_for_status()
                try:
                    result = resp.json()
                    if result.get("status") == "ERROR":
                        msg = f"错误 [{result.get('error_code', '?')}]: {result.get('error_msg', '')}"
                        _audit("send_gm_command", zone_id, command, f"FAIL:{msg}")
                        return {"ok": False, "error": msg}
                    msg = result.get("data") or result.get("error_msg") or "OK"
                except Exception:
                    msg = resp.text.strip() or "OK"

                _audit("send_gm_command", zone_id, command, f"OK:{msg}")
                return {"ok": True, "result": msg}

        except httpx.HTTPStatusError as e:
            err = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
            _audit("send_gm_command", zone_id, command, f"FAIL:{err}")
            return {"ok": False, "error": err}
        except Exception as e:
            _audit("send_gm_command", zone_id, command, f"FAIL:{e}")
            return {"ok": False, "error": str(e)}

    @registry.register(
        name="batch_send_gm_command",
        description="向多个区服批量发送同一 GM 指令（仅 admin 可用）。",
        input_schema={
            "type": "object",
            "properties": {
                "zone_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "目标区服 ID 列表",
                },
                "command": {"type": "string", "description": "GM 指令"},
                "merge_to": {"type": "integer", "description": "合区目标（默认 0）", "default": 0},
            },
            "required": ["zone_ids", "command"],
        },
        is_deterministic=False,
        allowed_roles=["admin"],
    )
    async def batch_send_gm_command(
        zone_ids: list[int], command: str, merge_to: int = 0
    ) -> dict:
        import asyncio as _asyncio
        import httpx

        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        base_url = cfg["base_url"].rstrip("/")
        url = f"{base_url}/api.php"

        async def _send_one(zone_id: int) -> tuple[int, str]:
            data = {
                "zoneid": str(zone_id),
                "mergeto": str(merge_to),
                "cmd": command,
                "explaints": "0",
                **_build_auth_data(cfg),
            }
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        url,
                        params={"mod": "GmTools", "act": "operateCmd"},
                        data=data,
                        cookies=_build_cookies(cfg),
                    )
                    resp.raise_for_status()
                    try:
                        r = resp.json()
                        if r.get("status") == "ERROR":
                            return zone_id, f"FAIL: {r.get('error_msg', '')}"
                        return zone_id, r.get("data") or "OK"
                    except Exception:
                        return zone_id, resp.text.strip() or "OK"
            except Exception as e:
                return zone_id, f"FAIL: {e}"

        results_raw = await _asyncio.gather(*[_send_one(z) for z in zone_ids])
        results = [{"zone_id": z, "result": r} for z, r in results_raw]
        ok_count = sum(1 for r in results if not r["result"].startswith("FAIL"))
        _audit("batch_send_gm_command", str(zone_ids), command, f"OK:{ok_count}/{len(zone_ids)}")
        return {"results": results, "ok": ok_count, "total": len(zone_ids)}

    @registry.register(
        name="copy_role",
        description=(
            "将角色从一个区服拷贝到另一个区服（仅 admin 可用）。"
            "export_zone_env 可选值: dev/and/ali/cnf/cnt"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "export_zone_env": {
                    "type": "string",
                    "description": "导出区环境前缀：dev / and / ali / cnf / cnt",
                    "enum": ["dev", "and", "ali", "cnf", "cnt"],
                },
                "export_zone_id": {"type": "integer", "description": "导出区服 ID"},
                "export_uid": {"type": "integer", "description": "导出角色 UID"},
                "import_zone_id": {"type": "integer", "description": "导入区服 ID"},
                "import_uid": {"type": "integer", "description": "导入角色 UID"},
                "import_type": {
                    "type": "string",
                    "description": "拷贝类型：all / role / mail（默认 all）",
                    "enum": ["all", "role", "mail"],
                    "default": "all",
                },
                "ignore_not_import": {
                    "type": "boolean",
                    "description": "忽略不重要数据（默认 false）",
                    "default": False,
                },
                "remove_invalid_act": {
                    "type": "boolean",
                    "description": "清空无效活动（默认 true）",
                    "default": True,
                },
            },
            "required": [
                "export_zone_env", "export_zone_id", "export_uid",
                "import_zone_id", "import_uid",
            ],
        },
        is_deterministic=False,
        allowed_roles=["admin"],
    )
    async def copy_role(
        export_zone_env: str,
        export_zone_id: int,
        export_uid: int,
        import_zone_id: int,
        import_uid: int,
        import_type: str = "all",
        ignore_not_import: bool = False,
        remove_invalid_act: bool = True,
    ) -> dict:
        import httpx
        try:
            cfg = _load_config()
        except Exception as e:
            return {"error": str(e)}

        base_url = cfg["base_url"].rstrip("/")
        url = f"{base_url}/index.php"

        data = {
            "exportzoneid": f"{export_zone_env}{export_zone_id}",
            "importzoneid": str(import_zone_id),
            "importtype": import_type,
            "roleuidexport": str(export_uid),
            "roleuidimport": str(import_uid),
            "ignorenotimport": "1" if ignore_not_import else "0",
            "removeinvalidact": "1" if remove_invalid_act else "0",
            **_build_auth_data(cfg),
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    params={"mod": "GmTools", "act": "importrole"},
                    data=data,
                    cookies=_build_cookies(cfg),
                )
                resp.raise_for_status()
                result_text = resp.text.strip() or "OK"
                ok = "ERROR" not in result_text.upper()
                _audit(
                    "copy_role",
                    f"{export_zone_id}->{import_zone_id}",
                    f"uid:{export_uid}->{import_uid}",
                    result_text,
                )
                return {"ok": ok, "result": result_text}

        except httpx.HTTPStatusError as e:
            err = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
            _audit("copy_role", f"{export_zone_id}->{import_zone_id}", "", f"FAIL:{err}")
            return {"ok": False, "error": err}
        except Exception as e:
            _audit("copy_role", f"{export_zone_id}->{import_zone_id}", "", f"FAIL:{e}")
            return {"ok": False, "error": str(e)}
