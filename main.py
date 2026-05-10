#!/usr/bin/env python3
"""
SFG_AGENT — 飞书游戏服务器 AI 助手
用法：python main.py [start|stop|restart|status|setup|logs]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time

# ──────────────────────────────────────────────────────────────────────────────
# 路径常量
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PID_FILE = os.path.join(DATA_DIR, "agent.pid")
LOG_FILE = os.path.join(DATA_DIR, "agent.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")


# ──────────────────────────────────────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(level: str = "INFO") -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s][%(name)s][%(levelname)s] %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 控制台
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件（轮转）
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger("main")


# ──────────────────────────────────────────────────────────────────────────────
# 配置加载
# ──────────────────────────────────────────────────────────────────────────────
def load_config(path: str = CONFIG_FILE):
    import re
    import yaml
    from pydantic import BaseModel, field_validator
    from typing import Optional

    if not os.path.exists(path):
        print(f"❌ 找不到配置文件：{path}")
        print("   请先运行：python main.py setup")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    # 展开 ${ENV_VAR} 语法
    def _expand(m):
        key = m.group(1)
        val = os.environ.get(key, "")
        if not val:
            print(f"⚠️  环境变量 {key} 未设置")
        return val

    raw = re.sub(r"\$\{([^}]+)\}", _expand, raw)
    data = yaml.safe_load(raw)

    class AnthropicCfg(BaseModel):
        api_key: str
        base_url: str
        model: str = "claude-sonnet-4-6"
        max_tokens: int = 4096

    class LarkCfg(BaseModel):
        bot_open_id: str
        lark_cli_path: str = ""

    class UserEntry(BaseModel):
        open_id: str
        name: str
        roles: list[str]

        @property
        def role(self) -> str:
            return self.roles[0] if self.roles else "viewer"

    class RoleCfg(BaseModel):
        allowed_tools: list[str] = ["*"]
        denied_tools: list[str] = []
        thinking_default: str = "low"

    class PermCfg(BaseModel):
        whitelist: list[UserEntry] = []

    class MemCfg(BaseModel):
        enabled: bool = True
        vector_enabled: bool = False
        ttl_days: int = 90

    class ThinkingCfg(BaseModel):
        default_level: str = "low"

    class GameCfg(BaseModel):
        enabled: bool = False
        gm_server_config: str = "~/.claude/mcp-servers/gm_server/config.json"
        audit_log: str = "~/sfg_agent/data/audit.log"

    class LogCfg(BaseModel):
        level: str = "INFO"

    class ProviderEntry(BaseModel):
        module: str
        enabled: bool = True

    class Config(BaseModel):
        anthropic: AnthropicCfg
        lark: LarkCfg
        permissions: PermCfg = PermCfg()
        roles: dict[str, RoleCfg] = {}
        memory: MemCfg = MemCfg()
        thinking: ThinkingCfg = ThinkingCfg()
        game: GameCfg = GameCfg()
        logging: LogCfg = LogCfg()
        tool_providers: list[ProviderEntry] = []

        def get_user(self, open_id: str) -> Optional[UserEntry]:
            for u in self.permissions.whitelist:
                if u.open_id == open_id:
                    return u
            return None

        def get_denied_tools(self, role: str) -> list[str]:
            r = self.roles.get(role)
            return r.denied_tools if r else []

    return Config(**data)


# ──────────────────────────────────────────────────────────────────────────────
# 主事件循环
# ──────────────────────────────────────────────────────────────────────────────
async def run_agent(config, tui_mode: bool = False) -> None:
    from agent.context_engine import ContextEngine
    from agent.memory_manager import MemoryManager
    from agent.orchestrator import Orchestrator
    from agent.prompt_builder import PromptBuilder
    from agent.thinking import ThinkingManager
    from gateway.feishu import FeishuGateway
    from handlers.mention import handle_mention
    from skills.curator import Curator
    from skills.dream import DreamCycle
    from skills.loader import SkillLoader
    from skills.skillify import Skillify
    from storage.cache_store import CacheStore
    from storage.minion_queue import MinionQueue
    from storage.session_store import SessionStore
    from tools.registry import registry

    # ── 初始化存储 ────────────────────────────────────────────────────────────
    session_store = SessionStore(ttl_days=7)
    await session_store.open()

    cache_dir = os.path.join(DATA_DIR, "cache")
    old_cache = os.path.expanduser("~/feishu_bot/cache")
    if os.path.isdir(old_cache) and not os.path.exists(cache_dir):
        os.symlink(old_cache, cache_dir)
        logger.info("已链接旧缓存：%s → %s", old_cache, cache_dir)
    cache_store = CacheStore(cache_dir=cache_dir)

    # ── 记忆后端（Phase 3：可选启用）────────────────────────────────────────
    memory_store = None
    if config.memory.enabled:
        from storage.memory_store import HybridMemoryStore
        memory_store = HybridMemoryStore()
        await memory_store.open()
        logger.info("HybridMemoryStore 已启用 (vector=%s)", config.memory.vector_enabled)

    # ── Anthropic 客户端（共享给 Skillify / DreamCycle）─────────────────────
    import anthropic as _anthropic
    shared_client = _anthropic.AsyncAnthropic(
        api_key=os.getenv("ANTHROPIC_AUTH_TOKEN") or config.anthropic.api_key,
        base_url=os.getenv("ANTHROPIC_BASE_URL") or config.anthropic.base_url,
    )

    # ── Agent 核心组件 ────────────────────────────────────────────────────────
    skill_loader = SkillLoader()
    memory_mgr = MemoryManager(store=memory_store)
    thinking = ThinkingManager(default_level=config.thinking.default_level)
    context_engine = ContextEngine()
    prompt_builder = PromptBuilder(config, skill_loader=skill_loader)
    # TeamManager 在工具注册后再初始化（需要 registry 的 provider_metas）

    # ── 跨群热力图（Group-Federated Skillify）────────────────────────────────
    from storage.group_heatmap import GroupHeatmap
    group_heatmap = GroupHeatmap()
    await group_heatmap.open()

    # ── Skillify + Curator + Dream（Phase 3）─────────────────────────────────
    _main_model = config.anthropic.model  # 统一从 config 读，不在各组件写死
    skillify = Skillify(client=shared_client, model=_main_model, heatmap=group_heatmap)
    curator = Curator(client=shared_client, model=_main_model)
    dream = DreamCycle(
        store=memory_store,
        client=shared_client,
        model=_main_model,
        heatmap=group_heatmap,
        skillify=skillify,
    )

    # ── Minion 队列（Phase 2）────────────────────────────────────────────────
    minion_queue = MinionQueue(concurrency=8)
    await minion_queue.start()

    # ── 工具 Provider 自动发现注册（插件式）─────────────────────────────────
    providers_cfg = getattr(config, "tool_providers", [])
    if providers_cfg:
        for p in providers_cfg:
            if getattr(p, "enabled", False):
                registry.load_provider(
                    p.module,
                    config=config,
                    memory_mgr=memory_mgr,
                    queue=minion_queue,
                )
    else:
        # 兜底：无 tool_providers 配置时沿用手动注册
        from tools.feishu_tools import register_feishu_tools
        from tools.memory_tools import register_memory_tools
        from tools.minion_tools import register_minion_tools
        register_feishu_tools(registry)
        register_memory_tools(registry, memory_mgr)
        register_minion_tools(registry, minion_queue)
        if config.game.enabled:
            from tools.game_tools import register_game_tools
            register_game_tools(registry)
    logger.info("工具注册完成，共 %d 个工具", len(registry._tools))

    # ── TeamManager 在工具注册后初始化（可读取已加载的 provider_metas）────────
    from agent.team_manager import TeamManager
    team_manager = TeamManager(registry=registry)

    # ── AgentDashboard（Hermes 风格实时看板）────────────────────────────────
    from agent.display import (AgentDashboard, build_welcome_banner,
                                print_startup_step, print_separator)
    dashboard = AgentDashboard(model=config.anthropic.model, tui_mode=tui_mode)

    # ── Orchestrator ─────────────────────────────────────────────────────────
    orchestrator = Orchestrator(
        config=config,
        session_store=session_store,
        memory_manager=memory_mgr,
        prompt_builder=prompt_builder,
        registry=registry,
        thinking=thinking,
        context_engine=context_engine,
        skillify=skillify,
        curator=curator,
        team_manager=team_manager,
        dashboard=dashboard,
    )

    # ── Rich 启动横幅 ─────────────────────────────────────────────────────────
    build_welcome_banner(
        model=config.anthropic.model,
        tool_count=len(registry._tools),
        whitelist_count=len(config.permissions.whitelist),
    )
    print_startup_step("SessionStore", "ok", "TTL 7d")
    print_startup_step("HybridMemoryStore", "ok" if memory_store else "skip",
                       "已启用" if memory_store else "未启用（memory.enabled=false）")
    print_startup_step("游戏工具", "ok" if config.game.enabled else "skip",
                       "gm_server 已注册" if config.game.enabled else "game.enabled=false")
    print_startup_step("MinionQueue", "ok", "concurrency=8")
    print_startup_step("GroupHeatmap", "ok", "跨群学习已启用")
    print_startup_step("Thinking", "ok", "adaptive ✓")
    print_startup_step("Prompt Cache", "ok", "ephemeral ✓")
    provider_count = len([p for p in getattr(config, "tool_providers", []) if p.enabled])
    print_startup_step("TeamManager", "ok", f"关键词路由 · {provider_count} 个 provider")
    print_separator()

    # 启动实时看板（TTY 下展示 Rich Live）
    dashboard.start()

    gateway = FeishuGateway(config)
    event_queue: asyncio.Queue = asyncio.Queue()

    logger.info(
        "SFG_AGENT 启动 (PID %d) | 模型：%s | 白名单：%d 人",
        os.getpid(), config.anthropic.model, len(config.permissions.whitelist),
    )

    # ── Dream 周期定时器（Phase 3）───────────────────────────────────────────
    async def dream_ticker():
        while True:
            await asyncio.sleep(3600)  # 每小时检查一次
            try:
                report = await dream.run(orchestrator._last_activity_at)
                if not report.get("skipped"):
                    logger.info("Dream 周期完成: %s", report.get("phases", {}))
            except Exception as e:
                logger.debug("Dream 周期异常: %s", e)

    # ── 消息消费者 ────────────────────────────────────────────────────────────
    async def consumer():
        while True:
            event = await event_queue.get()
            asyncio.create_task(
                handle_mention(
                    event=event,
                    gateway=gateway,
                    orchestrator=orchestrator,
                    session_store=session_store,
                    thinking=thinking,
                    config=config,
                    cache_store=cache_store,
                )
            )

    try:
        await asyncio.gather(
            gateway.subscribe(event_queue),
            consumer(),
            dream_ticker(),
        )
    finally:
        dashboard.stop()
        await minion_queue.stop()
        if memory_store:
            await memory_store.close()
        await group_heatmap.close()
        await session_store.close()


# ──────────────────────────────────────────────────────────────────────────────
# 进程管理
# ──────────────────────────────────────────────────────────────────────────────
def _read_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_start(daemon: bool = True) -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"✅ SFG_AGENT 已在运行 (PID {pid})")
        return

    if daemon and not os.environ.get("SFG_AGENT_DAEMON"):
        import subprocess

        env = os.environ.copy()
        env["SFG_AGENT_DAEMON"] = "1"
        log_fd = open(LOG_FILE, "a")
        proc = subprocess.Popen(
            [sys.executable, __file__, "start"],
            env=env,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1)
        print(f"🚀 SFG_AGENT 已在后台启动 (PID {proc.pid})")
        print(f"   日志：{LOG_FILE}")
        print(f"   停止：python main.py stop")
        return

    # 前台运行
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    config = load_config()
    setup_logging(config.logging.level)

    def _shutdown(sig, frame):
        logger.info("收到信号 %s，正在关闭…", sig)
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        asyncio.run(run_agent(config, tui_mode=False))
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)


def cmd_stop() -> None:
    pid = _read_pid()
    if not _is_running(pid):
        print("⚠️  SFG_AGENT 未运行")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not _is_running(pid):
            break
    if _is_running(pid):
        os.kill(pid, signal.SIGKILL)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    print(f"🛑 SFG_AGENT 已停止 (PID {pid})")


def cmd_status() -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"✅ SFG_AGENT 运行中 (PID {pid})")
        print(f"   日志：{LOG_FILE}")
    else:
        print("⭕ SFG_AGENT 未运行")


def cmd_logs() -> None:
    if not os.path.exists(LOG_FILE):
        print("暂无日志")
        return
    os.execvp("tail", ["tail", "-f", LOG_FILE])


def cmd_tui() -> None:
    """前台全屏神经指挥矩阵 TUI 模式（不 fork 子进程）"""
    config = load_config()
    setup_logging(config.logging.level)

    def _shutdown(sig, frame):
        logger.info("收到信号 %s，正在关闭…", sig)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    asyncio.run(run_agent(config, tui_mode=True))


def cmd_setup() -> None:
    """交互式配置向导"""
    import shutil
    example = os.path.join(BASE_DIR, "config.example.yaml")
    if not os.path.exists(example):
        print("❌ 找不到 config.example.yaml")
        sys.exit(1)

    if os.path.exists(CONFIG_FILE):
        ans = input("⚠️  config.yaml 已存在，覆盖？[y/N] ").strip().lower()
        if ans != "y":
            print("已取消")
            return

    shutil.copy(example, CONFIG_FILE)
    print("\n📝 配置向导（直接回车使用默认值）\n")

    def ask(prompt: str, default: str = "") -> str:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val or default

    api_key = ask("ANTHROPIC_AUTH_TOKEN (API Key)", os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
    base_url = ask("ANTHROPIC_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL", ""))
    bot_open_id = ask("飞书 Bot open_id", "")
    my_open_id = ask("你自己的 open_id (管理员)", "")
    my_name = ask("你的姓名", "管理员")

    # 写入 config.yaml
    with open(CONFIG_FILE, encoding="utf-8") as f:
        content = f.read()

    replacements = [
        ("${ANTHROPIC_AUTH_TOKEN}", api_key),
        ("${ANTHROPIC_BASE_URL}", base_url),
        ("ou_REPLACE_WITH_BOT_OPEN_ID", bot_open_id),
        ("ou_REPLACE_WITH_YOUR_OPEN_ID", my_open_id),
        ("管理员", my_name, 1),  # 只替换第一个
    ]
    for r in replacements:
        if len(r) == 3:
            content = content.replace(r[0], r[1], r[2])
        else:
            content = content.replace(r[0], r[1])

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n✅ 配置已保存：{CONFIG_FILE}")
    print("\n下一步：")
    print("  1. 确认飞书 Bot 已登录：lark-cli auth login --as bot")
    print("  2. 启动 Agent：python main.py start")
    print("  3. 在飞书群里 @Bot 测试\n")


# ──────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="sfg-agent",
        description="SFG_AGENT — 飞书游戏服务器 AI 助手",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=["start", "stop", "restart", "status", "setup", "logs", "tui"],
        help="管理命令（默认 start）",
    )
    args = parser.parse_args()

    match args.command:
        case "start":
            cmd_start()
        case "stop":
            cmd_stop()
        case "restart":
            cmd_stop()
            time.sleep(1)
            cmd_start()
        case "status":
            cmd_status()
        case "setup":
            cmd_setup()
        case "logs":
            cmd_logs()
        case "tui":
            cmd_tui()


if __name__ == "__main__":
    main()
