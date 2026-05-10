#!/usr/bin/env python3
"""
SFG_AGENT Setup Wizard v1.0
交互式初始化向导，每步均可跳过。

核心步骤固定（install / config / doctor / check），
工具步骤由 tools/*.py 中的 SETUP_META 自动发现注入到菜单。
新接一个工具，只需在该工具模块中声明 SETUP_META + setup_step()，无需修改本文件。

直接运行进入向导：
  python setup_agent.py

或执行单一命令：
  python setup_agent.py install   # 安装依赖
  python setup_agent.py config    # 配置向导
  python setup_agent.py doctor    # 环境诊断
  python setup_agent.py check     # 连接测试
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
HOME = Path.home()

# ─── Rich 展示（优雅降级）──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    console = Console()
    HAS_RICH = True

    def _ok(msg: str):   console.print(f"  [bold green]✓[/bold green] {msg}")
    def _warn(msg: str): console.print(f"  [bold yellow]⚠[/bold yellow] {msg}")
    def _err(msg: str):  console.print(f"  [bold red]✗[/bold red] {msg}")
    def _info(msg: str): console.print(f"  [dim]·[/dim] {msg}")
    def _skip(msg: str): console.print(f"  [dim]↷ 跳过：{msg}[/dim]")

    def _header(msg: str):
        console.print()
        console.rule(f"[bold cyan]{msg}[/bold cyan]")
        console.print()

    def _banner():
        console.print(Panel(
            "[bold cyan]  ███████╗███████╗ ██████╗      █████╗  ██████╗ ███████╗███╗  ██╗████████╗[/bold cyan]\n"
            "[bold cyan]  ██╔════╝██╔════╝██╔════╝     ██╔══██╗██╔════╝ ██╔════╝████╗ ██║╚══██╔══╝[/bold cyan]\n"
            "[bold cyan]  ███████╗█████╗  ██║  ███╗    ███████║██║  ███╗█████╗  ██╔██╗██║   ██║   [/bold cyan]\n"
            "[bold cyan]  ╚════██║██╔══╝  ██║   ██║    ██╔══██║██║   ██║██╔══╝  ██║╚████║   ██║   [/bold cyan]\n"
            "[bold cyan]  ███████║██║     ╚██████╔╝    ██║  ██║╚██████╔╝███████╗██║ ╚███║   ██║   [/bold cyan]\n"
            "[bold cyan]  ╚══════╝╚═╝      ╚═════╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚══╝   ╚═╝   [/bold cyan]\n\n"
            "[dim]飞书游戏服务器 AI 助手  ·  Setup Wizard v1.0[/dim]",
            title="[bold]SFG_AGENT[/bold]",
            border_style="cyan",
            padding=(1, 4),
        ))

except ImportError:
    HAS_RICH = False
    console = None

    def _ok(msg):   print(f"  ✓ {msg}")
    def _warn(msg): print(f"  ⚠ {msg}")
    def _err(msg):  print(f"  ✗ {msg}")
    def _info(msg): print(f"  · {msg}")
    def _skip(msg): print(f"  ↷ 跳过：{msg}")

    def _header(msg):
        print(f"\n{'─' * 50}")
        print(f"  {msg}")
        print(f"{'─' * 50}\n")

    def _banner():
        print("\n  ╔══════════════════════════════════════╗")
        print("  ║   SFG_AGENT  Setup Wizard  v1.0     ║")
        print("  ║   飞书游戏服务器 AI 助手初始化向导   ║")
        print("  ╚══════════════════════════════════════╝\n")


# ─── 输入工具 ─────────────────────────────────────────────────────────────────

def _ask(prompt: str, default: str = "", required: bool = False,
         secret: bool = False) -> str | None:
    """
    通用输入，返回值：
      None  → 用户输入 s/skip 表示跳过
      str   → 实际输入值（或 default）
    """
    hint = "[s=跳过]" if not required else "[必填]"
    display_default = ("*" * min(len(default), 8) + "…") if secret and default else default
    suffix = f" ({display_default})" if display_default else f" {hint}"

    while True:
        try:
            raw = input(f"  {prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if raw.lower() in ("s", "skip", "跳过"):
            return None
        if raw == "" and default:
            return default
        if raw == "" and required:
            _warn("此项必填，输入 s 可跳过整个步骤")
            continue
        return raw or default


def _confirm(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {prompt} {hint}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if raw in ("y", "yes", "是"):
        return True
    if raw in ("n", "no", "否"):
        return False
    return default


def _pause():
    try:
        input("\n  按 Enter 继续…")
    except (EOFError, KeyboardInterrupt):
        pass


# ─── Step 1：安装依赖 ─────────────────────────────────────────────────────────

def cmd_install(use_uv: bool = True) -> bool:
    _header("Step 1 · 安装 Python 依赖")

    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        _err(f"找不到 requirements.txt: {req_file}")
        return False

    if use_uv and shutil.which("uv"):
        cmd = ["uv", "pip", "install", "-r", str(req_file)]
        runner = "uv"
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(req_file)]
        runner = "pip"

    _info(f"使用 {runner} 安装依赖…")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        _err("安装失败，请检查上方错误信息")
        return False
    _ok("依赖安装完成")

    if _confirm("安装向量记忆支持（sentence-transformers，约 400MB，可选）"):
        vec_cmd = (["uv", "pip"] if runner == "uv" else [sys.executable, "-m", "pip"]) + [
            "install", "sentence-transformers", "numpy"
        ]
        subprocess.run(vec_cmd)
        _ok("向量记忆依赖安装完成")
    else:
        _skip("向量记忆")

    return True


# ─── Step 2：配置 Anthropic + 飞书 Bot ───────────────────────────────────────

def cmd_config() -> bool:
    _header("Step 2 · 基础配置（API Key / 飞书 Bot）")

    example = BASE_DIR / "config.example.yaml"
    target  = BASE_DIR / "config.yaml"

    if not example.exists():
        _err("找不到 config.example.yaml")
        return False

    if target.exists():
        if not _confirm("config.yaml 已存在，覆盖重新配置？"):
            _skip("config.yaml 已存在，保留现有配置")
            return True

    shutil.copy(example, target)
    _info("已从模板创建 config.yaml，请逐项填写（输入 s 跳过某项）")
    print()

    # ── Anthropic ──
    if HAS_RICH:
        console.print("  [bold]▸ Anthropic API[/bold]")
    else:
        print("  ▸ Anthropic API")

    api_key  = _ask("ANTHROPIC_AUTH_TOKEN", os.environ.get("ANTHROPIC_AUTH_TOKEN", ""), secret=True)
    base_url = _ask("ANTHROPIC_BASE_URL",   os.environ.get("ANTHROPIC_BASE_URL", ""))
    model    = _ask("模型名",               os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"))

    print()
    # ── 飞书 ──
    if HAS_RICH:
        console.print("  [bold]▸ 飞书 Bot[/bold]")
    else:
        print("  ▸ 飞书 Bot")

    _info("Bot open_id 可通过 'lark-cli whoami --as bot' 查询")
    bot_oid    = _ask("Bot open_id", "ou_")
    admin_oid  = _ask("管理员 open_id", "ou_")
    admin_name = _ask("管理员姓名", "管理员")

    print()
    # ── 游戏工具 ──
    if HAS_RICH:
        console.print("  [bold]▸ 游戏工具[/bold]")
    else:
        print("  ▸ 游戏工具")
    game_on = _confirm("启用游戏 GM 工具（需要配合 gm_server config.json）")

    # ── 写入文件 ──
    with open(target, encoding="utf-8") as f:
        content = f.read()

    replacements = {
        "${ANTHROPIC_AUTH_TOKEN}": api_key  or "${ANTHROPIC_AUTH_TOKEN}",
        "${ANTHROPIC_BASE_URL}":   base_url or "${ANTHROPIC_BASE_URL}",
        "${ANTHROPIC_MODEL}":      model    or "${ANTHROPIC_MODEL}",
        "ou_REPLACE_WITH_BOT_OPEN_ID":  bot_oid    or "ou_REPLACE_WITH_BOT_OPEN_ID",
        "ou_REPLACE_WITH_YOUR_OPEN_ID": admin_oid  or "ou_REPLACE_WITH_YOUR_OPEN_ID",
    }
    for old, new in replacements.items():
        content = content.replace(old, new)

    if admin_name and admin_name != "管理员":
        content = content.replace('"管理员"', f'"{admin_name}"', 1)

    if not game_on:
        # 将 game_tools enabled 改为 false
        lines = content.splitlines()
        in_game_section = False
        new_lines = []
        for line in lines:
            if "module: tools.game_tools" in line:
                in_game_section = True
            if in_game_section and "enabled: true" in line:
                line = line.replace("enabled: true", "enabled: false")
                in_game_section = False
            new_lines.append(line)
        content = "\n".join(new_lines)

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)

    _ok(f"配置已保存：{target}")
    return True


# ─── 插件步骤自动发现 ─────────────────────────────────────────────────────────

def _discover_tool_steps() -> list[tuple[str, str, object]]:
    """
    扫描 tools/*.py，找到带 SETUP_META 的模块，返回 (step_name, description, fn) 列表。
    新增工具只需在模块中声明 SETUP_META + setup_step()，无需修改本文件。
    """
    import importlib.util

    tools_dir = BASE_DIR / "tools"
    steps: list[tuple[str, str, object]] = []
    seen: set[str] = set()

    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.stem.startswith("_") or py_file.stem == "registry":
            continue
        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]

            meta = getattr(mod, "SETUP_META", None)
            if not meta or not isinstance(meta, dict):
                continue

            step_name = meta.get("step_name", py_file.stem)
            if step_name in seen:
                continue
            seen.add(step_name)

            fn_name = meta.get("setup_fn", "setup_step")
            fn = getattr(mod, fn_name, None)
            if not callable(fn):
                continue

            desc = meta.get("description", "")
            steps.append((step_name, desc, fn))
        except Exception as e:
            # 工具依赖未安装时静默跳过，不影响向导运行
            _info(f"跳过 {py_file.name} 的 setup 发现：{e}")

    return steps


# ─── Step 3+：工具插件步骤（由 SETUP_META 自动注入，保留为向后兼容入口）─────

def cmd_gm_config() -> bool:
    _header("Step 3 · 配置 GM 服务器（可选）")

    gm_dir    = HOME / ".claude/mcp-servers/gm_server"
    cfg_path  = gm_dir / "config.json"
    example_path = gm_dir / "config.json.example"

    _info("GM 服务器配置存放于：~/.claude/mcp-servers/gm_server/config.json")
    _info("游戏工具通过此配置直接 HTTP 调用 GM 后台 API")
    print()

    if cfg_path.exists():
        _ok(f"config.json 已存在：{cfg_path}")
        if not _confirm("重新配置 GM 服务器？"):
            _skip("保留现有 GM 配置")
            return True

    gm_dir.mkdir(parents=True, exist_ok=True)

    # ── 从 example 加载或从头开始 ──
    base_cfg: dict = {}
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                base_cfg = json.load(f)
        except Exception:
            pass

    if HAS_RICH:
        console.print("  [bold]▸ GM 后台连接信息[/bold]")
        console.print("  [dim]（输入 s 跳过某项，保留当前值）[/dim]")
    else:
        print("  ▸ GM 后台连接信息（输入 s 跳过某项）")
    print()

    base_url = _ask("GM 后台地址（如 http://192.168.1.1:8080）",
                    base_cfg.get("base_url", ""))
    token    = _ask("Token（与 username/password 二选一）",
                    base_cfg.get("token", ""), secret=True)
    username = _ask("用户名（有 token 可跳过）",
                    base_cfg.get("username", ""))
    password = _ask("密码（有 token 可跳过）",
                    base_cfg.get("password", ""), secret=True)
    nick     = _ask("昵称/Nick（写入 Cookie NICK）",
                    base_cfg.get("nick", ""))
    js_path  = _ask("GM 指令目录 JS 路径（用于 search_gm_commands，可跳过）",
                    base_cfg.get("gm_cmd_js_path", ""))

    new_cfg = {k: v for k, v in {
        "base_url":        base_url or base_cfg.get("base_url", ""),
        "token":           token    or base_cfg.get("token", ""),
        "username":        username or base_cfg.get("username", ""),
        "password":        password or base_cfg.get("password", ""),
        "nick":            nick     or base_cfg.get("nick", ""),
        "gm_cmd_js_path":  js_path  or base_cfg.get("gm_cmd_js_path", ""),
        "zones":           base_cfg.get("zones", []),
    }.items() if v}

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(new_cfg, f, ensure_ascii=False, indent=2)
    os.chmod(cfg_path, 0o600)

    # 同时写一份 example（无敏感值）
    example_cfg = {k: ("***" if k in ("token", "password") else v)
                   for k, v in new_cfg.items()}
    with open(example_path, "w", encoding="utf-8") as f:
        json.dump(example_cfg, f, ensure_ascii=False, indent=2)

    _ok(f"GM 配置已保存：{cfg_path}（权限 600）")
    return True


# ─── Step 4：飞书 Bot 登录 ────────────────────────────────────────────────────

def cmd_lark_login() -> bool:
    _header("Step 4 · 飞书 Bot 登录")

    lark = _find_lark_cli()
    if not lark:
        _err("未找到 lark-cli，请先安装：npm install -g @larksuite/lark-cli")
        _skip("飞书登录")
        return False

    _info(f"lark-cli：{lark}")

    # 检查是否已登录
    result = subprocess.run([lark, "whoami", "--as", "bot"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        _ok(f"Bot 已登录：{result.stdout.strip()[:80]}")
        if not _confirm("重新登录？"):
            _skip("保留现有登录状态")
            return True

    _info("即将打开飞书 Bot 登录流程…")
    _info("（如果是 headless 环境，会显示二维码或 URL，用手机扫码）")
    print()
    result = subprocess.run([lark, "auth", "login", "--as", "bot"])
    if result.returncode == 0:
        _ok("飞书 Bot 登录成功")
        return True
    else:
        _warn("登录可能未完成，请手动运行：lark-cli auth login --as bot")
        return False


# ─── Step 5：环境诊断 ─────────────────────────────────────────────────────────

def cmd_doctor() -> bool:
    _header("Step 5 · 环境诊断")

    issues = 0

    # Python 版本
    ver = sys.version_info
    if ver >= (3, 11):
        _ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")
    else:
        _err(f"Python {ver.major}.{ver.minor}.{ver.micro}（需要 3.11+）")
        _info("建议：uv python install 3.14 && uv run python main.py start")
        issues += 1

    # uv
    if shutil.which("uv"):
        _ok("uv 已安装")
    else:
        _warn("uv 未安装（推荐安装：curl -LsSf https://astral.sh/uv/install.sh | sh）")

    # lark-cli
    lark = _find_lark_cli()
    if lark:
        _ok(f"lark-cli：{lark}")
    else:
        _err("lark-cli 未找到 → npm install -g @larksuite/lark-cli")
        issues += 1

    # Python 包
    pkg_map = {
        "anthropic": "anthropic",
        "aiosqlite": "aiosqlite",
        "yaml":      "pyyaml",
        "pydantic":  "pydantic",
        "rich":      "rich",
        "httpx":     "httpx",
    }
    for mod, pkg in pkg_map.items():
        try:
            __import__(mod)
            _ok(f"  {pkg}")
        except ImportError:
            _err(f"  {pkg} 未安装")
            issues += 1

    # 环境变量
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        val = os.environ.get(key, "")
        if val:
            _ok(f"  ${key} 已设置")
        else:
            _warn(f"  ${key} 未设置（可在 config.yaml 中直接填写）")

    # config.yaml
    cfg_path = BASE_DIR / "config.yaml"
    if cfg_path.exists():
        _ok("config.yaml 存在")
    else:
        _warn("config.yaml 不存在 → 运行 Step 2 或 python setup_agent.py config")

    # GM config
    gm_cfg = HOME / ".claude/mcp-servers/gm_server/config.json"
    if gm_cfg.exists():
        _ok("GM config.json 存在")
    else:
        _info("GM config.json 不存在（未使用游戏工具则无需配置）")

    # data 目录
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _ok(f"data/ 目录就绪")

    print()
    if issues == 0:
        _ok("环境诊断通过！")
    else:
        _warn(f"发现 {issues} 个问题，请按提示处理后重新诊断")

    return issues == 0


# ─── Step 6：连接测试 ─────────────────────────────────────────────────────────

def cmd_check() -> bool:
    _header("Step 6 · 连接测试")

    sys.path.insert(0, str(BASE_DIR))
    config = None
    try:
        from main import load_config
        config = load_config()
        _ok("config.yaml 加载成功")
    except Exception as e:
        _err(f"配置加载失败：{e}")
        return False

    all_ok = True

    # Anthropic API
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_AUTH_TOKEN") or config.anthropic.api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL")  or config.anthropic.base_url,
        )
        client.messages.create(
            model=config.anthropic.model,
            max_tokens=10,
            messages=[{"role": "user", "content": "hello"}],
        )
        _ok(f"Anthropic API 连通（model={config.anthropic.model}）")
    except Exception as e:
        _err(f"Anthropic API 失败：{e}")
        all_ok = False

    # lark-cli bot 认证
    lark = _find_lark_cli() or getattr(config.lark, "lark_cli_path", "")
    if lark:
        result = subprocess.run([lark, "whoami", "--as", "bot"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            _ok(f"lark-cli bot 认证 OK：{result.stdout.strip()[:60]}")
        else:
            _warn("lark-cli bot 未认证 → lark-cli auth login --as bot")
            all_ok = False
    else:
        _warn("lark-cli 未找到，跳过飞书测试")

    # GM 服务器（可选）
    gm_cfg_path = HOME / ".claude/mcp-servers/gm_server/config.json"
    if gm_cfg_path.exists():
        try:
            with open(gm_cfg_path, encoding="utf-8") as f:
                gm_cfg = json.load(f)
            import httpx
            base = gm_cfg.get("base_url", "").rstrip("/")
            if base:
                resp = httpx.get(f"{base}/", timeout=5.0)
                _ok(f"GM 后台可达（HTTP {resp.status_code}）")
            else:
                _warn("GM config.json 缺少 base_url")
        except Exception as e:
            _warn(f"GM 后台连接失败：{e}")

    print()
    if all_ok:
        _ok("所有连接测试通过！")
    else:
        _warn("部分测试未通过，请按提示处理")

    return all_ok


# ─── 主向导 ───────────────────────────────────────────────────────────────────

# 固定核心步骤（始终存在）
_CORE_STEPS: list[tuple[str, str, object]] = [
    ("安装 Python 依赖",        "",  cmd_install),
    ("配置 API Key / 飞书 Bot", "",  cmd_config),
    ("环境诊断",                "",  cmd_doctor),
    ("连接测试",                "",  cmd_check),
]


def _build_steps() -> list[tuple[str, str, str, object]]:
    """
    构建完整步骤列表：核心步骤 + tools/ 中发现的插件步骤。
    返回 (key, label, description, fn)，key 为动态编号字符串。
    """
    steps: list[tuple[str, str, str, object]] = []

    # 核心步骤先放入
    for label, desc, fn in _CORE_STEPS:
        steps.append(("", label, desc, fn))  # key 稍后填

    # 扫描工具插件步骤，插入到核心步骤中间（诊断/测试之前）
    tool_steps = _discover_tool_steps()
    if tool_steps:
        # 在"配置 API"之后、"环境诊断"之前插入
        insert_at = 2
        for name, desc, fn in tool_steps:
            steps.insert(insert_at, ("", name, desc, fn))
            insert_at += 1

    # 按顺序分配编号
    numbered = []
    for i, (_, label, desc, fn) in enumerate(steps, start=1):
        numbered.append((str(i), label, desc, fn))

    return numbered


def _print_menu(steps: list[tuple[str, str, str, object]], completed: set[str]):
    print()
    if HAS_RICH:
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column(style="bold cyan", width=4)
        table.add_column(width=30)
        table.add_column(style="dim", width=28)
        table.add_column(style="dim", width=10)
        for key, label, desc, _ in steps:
            status = "[green]✓[/green]" if key in completed else ""
            table.add_row(f"[{key}]", label, desc[:26] + "…" if len(desc) > 26 else desc, status)
        table.add_row("[a]", "全部执行", "", "")
        table.add_row("[q]", "退出向导", "", "")
        console.print(table)
    else:
        for key, label, desc, _ in steps:
            status = "  ✓" if key in completed else ""
            tag = f"  · {desc[:40]}" if desc else ""
            print(f"  [{key}] {label}{tag}{status}")
        print("  [a] 全部执行")
        print("  [q] 退出向导")
    print()


def cmd_wizard() -> None:
    _banner()

    # 动态构建步骤（含工具插件）
    steps = _build_steps()
    max_key = str(len(steps))

    if HAS_RICH:
        console.print("  欢迎使用 [bold cyan]SFG_AGENT[/bold cyan] 初始化向导！")
        console.print(f"  共 [bold]{len(steps)}[/bold] 个步骤，每步均可单独执行。"
                      "  [dim]（输入 s 可跳过某个输入项）[/dim]\n")
    else:
        print(f"  欢迎使用 SFG_AGENT 初始化向导！共 {len(steps)} 个步骤。")
        print("  每步均可单独执行，输入 s 可跳过某个输入项。\n")

    completed: set[str] = set()

    while True:
        _print_menu(steps, completed)
        try:
            choice = input(f"  请选择步骤 [1-{max_key} / a=全部 / q=退出]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice in ("q", "quit", "exit", "退出"):
            break

        if choice in ("a", "all", "全部"):
            for key, label, _desc, fn in steps:
                _header(f"· {label}")
                try:
                    fn()
                    completed.add(key)
                except Exception as e:
                    _err(f"{label} 出错：{e}")
            print()
            _ok("全部步骤执行完毕！")
            _print_next_steps()
            break

        matched = [(k, l, d, f) for k, l, d, f in steps if k == choice]
        if not matched:
            _warn(f"无效选项：{choice!r}，请输入 1-{max_key}、a 或 q")
            continue

        key, label, _desc, fn = matched[0]
        _header(f"· {label}")
        try:
            fn()
            completed.add(key)
        except Exception as e:
            _err(f"{label} 出错：{e}")

        if len(completed) == len(steps):
            print()
            _ok("所有步骤已完成！")
            _print_next_steps()
            break


def _print_next_steps():
    print()
    if HAS_RICH:
        console.rule("[bold green]安装完成[/bold green]")
        console.print("\n  后续操作：")
        console.print("    [bold]python main.py start[/bold]  ← 启动 Agent")
        console.print("    [bold]python main.py logs[/bold]   ← 查看实时日志")
        console.print("    [bold]python main.py status[/bold] ← 查看运行状态")
        console.print("\n  在飞书群 [bold cyan]@Bot[/bold cyan] 即可开始对话。\n")
    else:
        print("\n  ── 安装完成 ──")
        print("  python main.py start   ← 启动")
        print("  python main.py logs    ← 日志")
        print("  在飞书群 @Bot 即可对话\n")


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _find_lark_cli() -> str:
    candidates = [
        shutil.which("lark-cli"),
        str(HOME / "nodejs/node22.15/bin/lark-cli"),
        str(HOME / "nodejs/node20/bin/lark-cli"),
        str(HOME / ".npm-global/bin/lark-cli"),
        str(HOME / ".local/bin/lark-cli"),
    ]
    for p in candidates:
        if p and Path(p).is_file():
            return p
    return ""


# ─── new-tool 脚手架 ──────────────────────────────────────────────────────────

# 各类型工具的文件模板
_TOOL_TEMPLATES: dict[str, str] = {

"http": '''\
"""
{display_name} 工具集
HTTP API 直连（在此填写 API 说明）
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

PROVIDER_META: dict = {{
    "name": "{name}_tools",
    "description": "{display_name}",
    "register_fn": "register_{name}_tools",
    "domain_name": "{domain}",
    "domain_keywords": {keywords_repr},
    "tool_name_prefixes": ["{name}_"],
    "weight": 2,
    "requires_config_key": None,
}}

SETUP_META: dict = {{
    "step_name": "配置 {display_name}",
    "description": "填写 {display_name} 的连接信息",
    "optional": True,
    "setup_fn": "setup_step",
}}


def setup_step() -> bool:
    """配置 {display_name} 连接信息，仅使用 stdlib。"""
    import json as _json, os as _os
    from pathlib import Path as _Path

    cfg_dir  = _Path.home() / ".claude/mcp-servers/{name}"
    cfg_path = cfg_dir / "config.json"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    existing: dict = {{}}
    if cfg_path.exists():
        try:
            existing = _json.loads(cfg_path.read_text())
        except Exception:
            pass

    def _ask(prompt, default=""):
        raw = input(f"  {{prompt}} ({{default or 's=跳过'}}): ").strip()
        return None if raw.lower() in ("s", "skip") else (raw or default or None)

    base_url = _ask("{display_name} API 地址", existing.get("base_url", ""))
    token    = _ask("Token / API Key", existing.get("token", ""))

    cfg = {{k: v for k, v in {{"base_url": base_url, "token": token, **existing}}.items() if v}}
    cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2))
    _os.chmod(cfg_path, 0o600)
    print(f"  ✓ 配置已保存：{{cfg_path}}")
    return True


def _load_config() -> dict:
    from pathlib import Path
    import json
    p = Path.home() / ".claude/mcp-servers/{name}/config.json"
    if not p.exists():
        raise RuntimeError(f"配置文件不存在：{{p}}，请先运行 setup_agent.py 配置")
    return json.loads(p.read_text())


def register_{name}_tools(registry: "ToolRegistry") -> None:

    @registry.register(
        name="{name}_action",
        description="{display_name} - 示例动作（请修改）",
        input_schema={{
            "type": "object",
            "properties": {{
                "param": {{"type": "string", "description": "参数说明"}},
            }},
            "required": ["param"],
        }},
    )
    async def {name}_action(param: str) -> dict:
        import httpx
        cfg = _load_config()
        base_url = cfg["base_url"].rstrip("/")
        headers = {{"Authorization": f"Bearer {{cfg.get(\'token\', \'\')}}"}}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{{base_url}}/api/action", params={{"param": param}},
                                    headers=headers)
            resp.raise_for_status()
            return resp.json()
''',

"cli": '''\
"""
{display_name} 工具集
命令行工具封装（在此填写 CLI 说明）
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

PROVIDER_META: dict = {{
    "name": "{name}_tools",
    "description": "{display_name}",
    "register_fn": "register_{name}_tools",
    "domain_name": "{domain}",
    "domain_keywords": {keywords_repr},
    "tool_name_prefixes": ["{name}_"],
    "weight": 2,
}}

SETUP_META: dict = {{
    "step_name": "检查 {display_name} CLI",
    "description": "验证 {name} 命令行工具是否可用",
    "optional": True,
    "setup_fn": "setup_step",
}}


def setup_step() -> bool:
    import shutil
    cli = shutil.which("{name}")
    if cli:
        print(f"  ✓ {name} 已找到：{{cli}}")
        return True
    print("  ✗ 未找到 {name}，请先安装：")
    print("    # TODO: 填写安装命令")
    return False


def register_{name}_tools(registry: "ToolRegistry") -> None:

    @registry.register(
        name="{name}_run",
        description="{display_name} - 执行命令（请修改）",
        input_schema={{
            "type": "object",
            "properties": {{
                "args": {{"type": "string", "description": "命令参数"}},
            }},
            "required": ["args"],
        }},
    )
    async def {name}_run(args: str) -> dict:
        import asyncio, shlex
        proc = await asyncio.create_subprocess_exec(
            "{name}", *shlex.split(args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return {{
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
        }}
''',

"mcp": '''\
"""
{display_name} 工具集
MCP 服务器客户端（通过 mcp Python SDK 调用）
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

PROVIDER_META: dict = {{
    "name": "{name}_tools",
    "description": "{display_name}",
    "register_fn": "register_{name}_tools",
    "domain_name": "{domain}",
    "domain_keywords": {keywords_repr},
    "tool_name_prefixes": ["{name}_"],
    "weight": 2,
    "requires_config_key": None,
}}

SETUP_META: dict = {{
    "step_name": "配置 {display_name} MCP 服务器",
    "description": "填写 MCP 服务器命令或地址",
    "optional": True,
    "setup_fn": "setup_step",
}}

# MCP 服务器启动命令（按实际情况修改）
_MCP_COMMAND = ["python", "-m", "{name}_mcp_server"]
_MCP_ENV: dict = {{}}


def setup_step() -> bool:
    import json as _json, os as _os
    from pathlib import Path as _Path

    cfg_dir  = _Path.home() / ".claude/mcp-servers/{name}"
    cfg_path = cfg_dir / "config.json"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    def _ask(prompt, default=""):
        raw = input(f"  {{prompt}} ({{default or 's=跳过'}}): ").strip()
        return None if raw.lower() in ("s", "skip") else (raw or default or None)

    cmd = _ask("MCP 服务器启动命令", " ".join(_MCP_COMMAND))
    env = _ask("额外环境变量（key=val,key=val，可跳过）", "")

    cfg = {{"command": cmd or " ".join(_MCP_COMMAND)}}
    if env:
        cfg["env"] = dict(pair.split("=", 1) for pair in env.split(",") if "=" in pair)
    cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2))
    _os.chmod(cfg_path, 0o600)
    print(f"  ✓ MCP 配置已保存：{{cfg_path}}")
    return True


async def _mcp_call(tool_name: str, arguments: dict) -> str:
    """通用 MCP 工具调用（stdio 模式）。"""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return '{{"error": "需要安装 mcp 库：uv pip install mcp"}}'

    import json
    from pathlib import Path
    cfg_path = Path.home() / ".claude/mcp-servers/{name}/config.json"
    cmd_str  = json.loads(cfg_path.read_text()).get("command", "") if cfg_path.exists() else ""
    cmd_parts = cmd_str.split() if cmd_str else _MCP_COMMAND

    server_params = StdioServerParameters(command=cmd_parts[0], args=cmd_parts[1:])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return str(result.content[0].text if result.content else "")


def register_{name}_tools(registry: "ToolRegistry") -> None:

    @registry.register(
        name="{name}_mcp_action",
        description="{display_name} MCP - 示例工具（请修改或根据服务器工具列表扩展）",
        input_schema={{
            "type": "object",
            "properties": {{
                "param": {{"type": "string", "description": "参数说明"}},
            }},
            "required": ["param"],
        }},
    )
    async def {name}_mcp_action(param: str) -> dict:
        result = await _mcp_call("your_mcp_tool_name", {{"param": param}})
        return {{"result": result}}
''',

"skill": '''\
# {display_name} Skill
---
name: {name}
description: {display_name} 使用技能
type: reference
---

## 概述

{display_name} 的使用说明和最佳实践。

## 常用操作

### 操作一：示例
```
# 在此填写示例
```

## 注意事项

- 注意事项一
- 注意事项二

## 排错指南

**Q: 常见问题**
- 解决方案
''',
}


def cmd_new_tool() -> None:
    """交互式生成新工具/Skill 模板文件。"""
    _header("新工具 / Skill 脚手架")

    if HAS_RICH:
        console.print("  根据提示填写信息，自动生成模板文件。\n"
                      "  [dim]生成后只需修改标注 TODO 的部分即可运行。[/dim]\n")
    else:
        print("  根据提示生成模板文件，修改 TODO 部分即可运行。\n")

    # ── 工具类型 ──────────────────────────────────────────────────────────────
    if HAS_RICH:
        console.print("  工具类型：")
        console.print("    [1] HTTP API 工具  · 直接调用 REST 接口")
        console.print("    [2] 命令行工具      · 封装 CLI 命令（如 lark-cli）")
        console.print("    [3] MCP 工具        · 连接 MCP 服务器（stdio 模式）")
        console.print("    [4] Skill 文档      · 创建 ~/.claude/skills/ 知识文档\n")
    else:
        print("  [1] HTTP API  [2] 命令行  [3] MCP 服务器  [4] Skill 文档\n")

    type_map = {"1": "http", "2": "cli", "3": "mcp", "4": "skill"}
    while True:
        choice = input("  选择类型 [1-4]: ").strip()
        if choice in type_map:
            tool_type = type_map[choice]
            break
        _warn("请输入 1-4")

    # ── 基本信息 ──────────────────────────────────────────────────────────────
    print()
    name = _ask("工具名（小写字母+下划线，如 wiki / slack / jira）", required=True)
    if not name:
        _warn("已取消")
        return
    name = name.lower().replace("-", "_").strip()

    display_name = _ask(f"显示名称（如 飞书知识库）", name)
    display_name = display_name or name

    if tool_type != "skill":
        domain  = _ask("领域名（用于路由，如 wiki/game/feishu）", name)
        domain  = domain or name
        kw_raw  = _ask("关键词（逗号分隔，如 知识库,wiki,文档）", name)
        keywords = [k.strip() for k in (kw_raw or name).split(",") if k.strip()]
    else:
        domain, keywords = "", []

    # ── 生成文件 ──────────────────────────────────────────────────────────────
    keywords_repr = repr(keywords)
    template = _TOOL_TEMPLATES[tool_type]
    content = template.format(
        name=name, display_name=display_name,
        domain=domain, keywords_repr=keywords_repr,
    )

    if tool_type == "skill":
        # 写 SKILL.md
        skill_dir = HOME / f".claude/skills/{name}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        out_path = skill_dir / "SKILL.md"
        out_path.write_text(content, encoding="utf-8")
        _ok(f"Skill 文档已生成：{out_path}")
        _info("在飞书群使用时，Agent 会自动加载此 Skill")

    else:
        # 写工具文件
        out_path = BASE_DIR / "tools" / f"{name}_tools.py"
        if out_path.exists():
            ans = input(f"  {out_path.name} 已存在，覆盖？[y/N]: ").strip().lower()
            if ans != "y":
                _skip("保留现有文件")
                return
        out_path.write_text(content, encoding="utf-8")
        _ok(f"工具文件已生成：{out_path}")

        # 自动追加到 config.yaml
        cfg_path = BASE_DIR / "config.yaml"
        if cfg_path.exists():
            cfg_text = cfg_path.read_text(encoding="utf-8")
            entry = f"  - module: tools.{name}_tools\n    enabled: true"
            if f"tools.{name}_tools" not in cfg_text:
                # 追加到 tool_providers 末尾
                cfg_text = cfg_text.rstrip() + f"\n{entry}\n"
                cfg_path.write_text(cfg_text, encoding="utf-8")
                _ok(f"已添加到 config.yaml tool_providers")
            else:
                _info(f"config.yaml 中已存在 tools.{name}_tools，跳过")
        else:
            _warn("config.yaml 不存在，请手动将以下内容添加到 tool_providers：")
            _info(f"  - module: tools.{name}_tools")
            _info(f"    enabled: true")

        print()
        if HAS_RICH:
            console.print("  [bold green]下一步：[/bold green]")
            console.print(f"    1. 编辑 [bold]{out_path}[/bold]，修改 TODO 部分")
            console.print(f"    2. 运行 [bold]python main.py restart[/bold] 重新加载")
            if tool_type == "http" or tool_type == "mcp":
                console.print(f"    3. 运行 [bold]python setup_agent.py[/bold] → 选择 "
                               f"\"配置 {display_name}\" 步骤填写连接信息")
        else:
            print(f"  下一步：")
            print(f"    1. 编辑 {out_path}，修改 TODO 部分")
            print(f"    2. python main.py restart")


# ─── CLI 入口 ─────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="setup_agent",
        description="SFG_AGENT 初始化向导",
        epilog="不带参数直接运行进入交互式向导；工具插件步骤由 tools/*.py 中的 SETUP_META 自动注入",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="wizard",
        choices=["wizard", "install", "config", "gm", "lark",
                 "doctor", "check", "new-tool"],
        help="命令（默认进入向导）",
    )
    args = parser.parse_args()

    dispatch = {
        "wizard":   cmd_wizard,
        "install":  cmd_install,
        "config":   cmd_config,
        "gm":       cmd_gm_config,
        "lark":     cmd_lark_login,
        "doctor":   cmd_doctor,
        "check":    cmd_check,
        "new-tool": cmd_new_tool,
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
