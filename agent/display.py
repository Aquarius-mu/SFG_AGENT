"""
Rich 终端展示层 v1.0
- AgentDashboard: 神经指挥矩阵 TUI（科技感全屏/紧凑双模式）
- build_welcome_banner: Hermes 风格启动横幅
- KawaiiSpinner: 带动画的任务进度提示
- format_tool_call: 单行工具调用展示
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    from rich.console import Console, Group as RGroup
    from rich.columns import Columns
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.rule import Rule
    from rich.spinner import Spinner
    from rich import box as rich_box
    from rich.box import Box

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

console = Console() if _RICH_AVAILABLE else None

# ─── 神经指挥矩阵 色彩系统 ────────────────────────────────────────────────────

_C_PRIMARY  = "bright_cyan"        # 主边框 · 标题
_C_ACTIVE   = "bright_green"       # 活跃 · 成功
_C_METRIC   = "yellow"             # 指标数据
_C_ERROR    = "bright_red"         # 错误 · 告警
_C_ACCENT   = "cyan"               # 次要高亮
_C_DIM      = "dim"                # 淡化
_C_TEXT     = "white"              # 正文

# ─── 动画帧序列 ───────────────────────────────────────────────────────────────

_BEACON   = ["◐", "◓", "◑", "◒"]                          # 旋转信标
_PULSE    = ["█", "▓", "▒", "░", "▒", "▓"]                 # 脉冲扫描
_SPINCHAR = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_BARS     = " ▏▎▍▌▋▊▉█"                                    # 8 级进度条

# ─── 自定义边框 ────────────────────────────────────────────────────────────────

_DOUBLE = rich_box.DOUBLE        # ╔═╗ 双线框，用于主面板
_SIMPLE = rich_box.SIMPLE_HEAD   # 用于子表格

# ─── 工具图标映射 ─────────────────────────────────────────────────────────────

_TOOL_ICON: dict[str, str] = {
    "list_zones":      "◈",
    "send_gm":         "⚡",
    "batch_send":      "⚡⚡",
    "get_user":        "◎",
    "send_message":    "▷",
    "search_group":    "◉",
    "remember":        "△",
    "recall":          "▽",
    "forget":          "✕",
    "search_memory":   "◇",
    "copy_role":       "⊞",
    "ping_zone":       "◌",
    "feishu":          "▷",
    "game":            "⚡",
    "memory":          "△",
}
_DEFAULT_ICON = "◆"


def _tool_icon(name: str) -> str:
    for k, v in _TOOL_ICON.items():
        if k in name.lower():
            return v
    return _DEFAULT_ICON


def _bar(ratio: float, width: int = 8) -> str:
    """把 0.0-1.0 映射成 Unicode 块进度条。"""
    filled = min(ratio, 1.0) * width
    full   = int(filled)
    frac   = filled - full
    bar    = "█" * full
    if frac > 0.1 and full < width:
        bar += _BARS[int(frac * 8)]
    bar += "░" * (width - len(bar))
    return bar


# ─── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class _ConvRecord:
    chat_id:    str
    group:      str
    sender:     str
    user_input: str
    response:   str   = ""
    elapsed_ms: int   = 0
    tool_count: int   = 0
    tokens:     int   = 0
    status:     str   = "active"   # active / done / error
    ts:         float = field(default_factory=time.time)


# ─── AgentDashboard ──────────────────────────────────────────────────────────

_SFG_LOGO = """\
[bold bright_cyan] ╔═══╗ ╔═══╗ ╔═══╗   ╔═╗  ╔═══╗ ╔═══╗ ╔═╗  ╔╦╗
 ╚══╗║ ╠═╦═╝ ║╔══╝   ╠═╩╗ ║╔═╗║ ║╔══╝ ║ ╚╗ ║║║
 ╔══╝║ ║ ║   ║║╔═╗   ║╔╗║ ║║ ╚╝ ║╠══  ║╔╗╚╗║ ║
 ╚═══╝ ╚═╝   ╚╩╩═╝   ╚╝╚╝ ╚╝    ╚╩══╝ ╚╝╚═╝╚ ╩[/bold bright_cyan]"""

_SFG_LOGO_COMPACT = "[bold bright_cyan]SFG AGENT[/bold bright_cyan]"


class AgentDashboard:
    """
    神经指挥矩阵 — 科技感终端看板。

    两种模式：
    - tui_mode=True  : screen=True，全屏接管，用于 `main.py tui`
    - tui_mode=False : screen=False，紧凑侧边栏，用于 `main.py start` 前台模式
    """

    def __init__(self, model: str = "", version: str = "1.0",
                 tui_mode: bool = False):
        self._model     = model
        self._version   = version
        self._tui_mode  = tui_mode
        self._start     = time.time()
        self._lock      = threading.Lock()
        self._frame     = 0

        self._active:   dict[str, _ConvRecord]            = {}
        self._history:  deque[_ConvRecord]                 = deque(maxlen=5)
        self._tool_log: deque[tuple[str, int, str, float]] = deque(maxlen=12)
        self._group_hits: dict[str, int]                   = defaultdict(int)
        self._stats     = {"msg": 0, "tools": 0, "tokens": 0, "errors": 0,
                           "total_ms": 0, "turns": 0}

        self._live: Optional[Live] = None
        self._enabled = _RICH_AVAILABLE and (tui_mode or sys.stdout.isatty())

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled:
            return
        self._live = Live(
            self._render(),
            refresh_per_second=4,
            screen=self._tui_mode,
            vertical_overflow="visible" if not self._tui_mode else "ellipsis",
            console=Console(stderr=False),
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    # ── 事件钩子（线程安全）──────────────────────────────────────────────────

    def on_request(self, chat_id: str, group: str,
                   sender: str, text: str) -> None:
        with self._lock:
            self._active[chat_id] = _ConvRecord(
                chat_id=chat_id, group=group,
                sender=sender, user_input=text[:72],
            )
            self._stats["msg"]      += 1
            self._group_hits[group] += 1
        self._refresh()

    def on_tool(self, chat_id: str, tool_name: str,
                elapsed_ms: int = 0) -> None:
        with self._lock:
            if chat_id in self._active:
                self._active[chat_id].tool_count += 1
            group = self._active[chat_id].group if chat_id in self._active else ""
            self._tool_log.append((tool_name, elapsed_ms, group, time.time()))
            self._stats["tools"] += 1
        self._refresh()

    def on_done(self, chat_id: str, response: str = "",
                tokens: int = 0, elapsed_ms: int = 0) -> None:
        with self._lock:
            rec = self._active.pop(chat_id, None)
            if rec:
                rec.response   = response[:90]
                rec.elapsed_ms = elapsed_ms
                rec.tokens     = tokens
                rec.status     = "done"
                self._history.append(rec)
            self._stats["tokens"]   += tokens
            self._stats["total_ms"] += elapsed_ms
            self._stats["turns"]    += 1
        self._refresh()

    def on_error(self, chat_id: str, error: str = "") -> None:
        with self._lock:
            rec = self._active.pop(chat_id, None)
            if rec:
                rec.status   = "error"
                rec.response = error[:90]
                self._history.append(rec)
            self._stats["errors"] += 1
        self._refresh()

    # ── 渲染入口 ──────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._frame += 1
        if self._live:
            self._live.update(self._render())

    def _render(self):
        if self._tui_mode:
            return self._render_fullscreen()
        return self._render_compact()

    # ══════════════════════════════════════════════════════════════════════════
    # 全屏模式（神经指挥矩阵）
    # ══════════════════════════════════════════════════════════════════════════

    def _render_fullscreen(self):
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=7),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="comm",     ratio=5),
            Layout(name="dispatch", ratio=4),
            Layout(name="intel",    ratio=3),
        )
        layout["header"].update(self._panel_header(full=True))
        layout["comm"].update(self._panel_comm())
        layout["dispatch"].update(self._panel_dispatch())
        layout["intel"].update(self._panel_intel())
        layout["footer"].update(self._panel_footer())
        return layout

    # ══════════════════════════════════════════════════════════════════════════
    # 紧凑模式（随日志滚动）
    # ══════════════════════════════════════════════════════════════════════════

    def _render_compact(self):
        return Panel(
            RGroup(
                Columns([
                    self._panel_comm(),
                    self._panel_dispatch(),
                    self._panel_intel(),
                ], equal=True, expand=True),
            ),
            title=self._header_title(full=False),
            border_style=_C_PRIMARY,
            padding=(0, 1),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # 子面板
    # ══════════════════════════════════════════════════════════════════════════

    def _header_title(self, full: bool = False) -> str:
        beacon = _BEACON[self._frame % len(_BEACON)]
        elapsed = int(time.time() - self._start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        uptime  = f"{h}h{m:02d}m" if h else f"{m:02d}m{s:02d}s"

        with self._lock:
            active_n = len(self._active)
        status = (
            f"[{_C_ACTIVE}]◉ {active_n} PROCESSING[/{_C_ACTIVE}]"
            if active_n else
            f"[{_C_DIM}]○ STANDBY[/{_C_DIM}]"
        )
        return (
            f"[{_C_PRIMARY}]{beacon}[/{_C_PRIMARY}] "
            f"[bold {_C_PRIMARY}]SFG AGENT[/bold {_C_PRIMARY}]  "
            f"[{_C_DIM}]v{self._version}[/{_C_DIM}]  "
            f"[{_C_ACCENT}]{self._model}[/{_C_ACCENT}]  "
            f"[{_C_DIM}]↑{uptime}[/{_C_DIM}]  "
            f"{status}"
        )

    def _panel_header(self, full: bool = True) -> Panel:
        beacon = _BEACON[self._frame % len(_BEACON)]
        pulse  = _PULSE[self._frame % len(_PULSE)]
        elapsed = int(time.time() - self._start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        uptime  = f"{h:02d}:{m:02d}:{s:02d}"

        with self._lock:
            active_n = len(self._active)
            st = self._stats.copy()

        # 状态灯
        status_glyph = f"[{_C_ACTIVE}]◉[/{_C_ACTIVE}]" if active_n else f"[{_C_DIM}]○[/{_C_DIM}]"
        status_txt   = f"[{_C_ACTIVE}]PROCESSING  ×{active_n}[/{_C_ACTIVE}]" if active_n else f"[{_C_DIM}]STANDBY[/{_C_DIM}]"

        # 右侧快速指标
        avg_ms = (st["total_ms"] // st["turns"]) if st["turns"] else 0
        quick  = (
            f"[{_C_METRIC}]{st['msg']:>5}[/{_C_METRIC}] MSG    "
            f"[{_C_METRIC}]{st['tools']:>5}[/{_C_METRIC}] TOOL   "
            f"[{_C_METRIC}]{st['tokens']//1000:>4}k[/{_C_METRIC}] TKN   "
            f"[{_C_METRIC}]{avg_ms:>5}[/{_C_METRIC}] ms/avg"
        )

        # 扫描线动画
        scan_pos  = self._frame % 40
        scan_line = (
            f"[{_C_DIM}]{'─' * scan_pos}[/{_C_DIM}]"
            f"[{_C_PRIMARY}]{pulse}[/{_C_PRIMARY}]"
            f"[{_C_DIM}]{'─' * (39 - scan_pos)}[/{_C_DIM}]"
        )

        grid = Table.grid(expand=True)
        grid.add_column(ratio=3)
        grid.add_column(ratio=1, justify="right")
        grid.add_row(
            Text.from_markup(_SFG_LOGO),
            Text.from_markup(
                f"[{_C_DIM}]NEURAL COMMAND MATRIX[/{_C_DIM}]\n"
                f"[bold {_C_PRIMARY}]{beacon}[/bold {_C_PRIMARY}] "
                f"{status_glyph} {status_txt}\n"
                f"[{_C_DIM}]UPLINK  [/{_C_DIM}][{_C_PRIMARY}]{uptime}[/{_C_PRIMARY}]\n"
                f"[{_C_DIM}]MODEL   [/{_C_DIM}][{_C_ACCENT}]{self._model[:28]}[/{_C_ACCENT}]"
            ),
        )

        return Panel(
            RGroup(grid, Text.from_markup(f"\n  {scan_line}  {quick}")),
            border_style=_C_PRIMARY,
            box=_DOUBLE,
            padding=(0, 2),
        )

    # ─── COMM SIGNALS（对话面板）────────────────────────────────────────────

    def _panel_comm(self) -> Panel:
        tbl = Table(box=None, show_header=False, padding=(0, 1),
                    expand=True, show_edge=False)
        tbl.add_column(style=_C_DIM,     width=5,  no_wrap=True)
        tbl.add_column(style=_C_PRIMARY, width=12, no_wrap=True)
        tbl.add_column(overflow="fold")

        with self._lock:
            records = list(self._history) + list(self._active.values())

        for rec in records[-6:]:
            ts = datetime.fromtimestamp(rec.ts).strftime("%H:%M")
            if rec.status == "active":
                spin  = _SPINCHAR[self._frame % len(_SPINCHAR)]
                icon  = f"[{_C_ACTIVE}]{spin}[/{_C_ACTIVE}]"
                body  = (
                    f"[{_C_TEXT}]{rec.user_input[:50]}[/{_C_TEXT}]\n"
                    f"  [{_C_ACTIVE}]◉ PROCESSING...[/{_C_ACTIVE}]"
                    + (f"  [{_C_DIM}](tools×{rec.tool_count})[/{_C_DIM}]"
                       if rec.tool_count else "")
                )
            elif rec.status == "error":
                icon = f"[{_C_ERROR}]✕[/{_C_ERROR}]"
                body = (
                    f"[{_C_DIM}]{rec.user_input[:50]}[/{_C_DIM}]\n"
                    f"  [{_C_ERROR}]⚠ {rec.response[:55]}[/{_C_ERROR}]"
                )
            else:
                icon = f"[{_C_ACTIVE}]✓[/{_C_ACTIVE}]"
                ms   = f"[{_C_DIM}]{rec.elapsed_ms}ms[/{_C_DIM}]" if rec.elapsed_ms else ""
                tool_tag = f"  [{_C_DIM}]×{rec.tool_count}[/{_C_DIM}]" if rec.tool_count else ""
                body = (
                    f"[{_C_DIM}]{rec.user_input[:50]}[/{_C_DIM}]\n"
                    f"  [{_C_TEXT}]{rec.response[:55]}[/{_C_TEXT}]{tool_tag}  {ms}"
                )

            gid = f"[bold {_C_PRIMARY}]{rec.group[:10]}[/bold {_C_PRIMARY}]"
            tbl.add_row(f"{icon} {ts}", gid,
                        f"[{_C_DIM}]{rec.sender[:8]}[/{_C_DIM}]\n{body}\n")

        if not records:
            tbl.add_row("", "",
                        f"[{_C_DIM}]── 等待信号接入 ──[/{_C_DIM}]")

        return Panel(
            tbl,
            title=f"[bold {_C_PRIMARY}]◈ COMM SIGNALS[/bold {_C_PRIMARY}]",
            border_style=_C_PRIMARY,
            box=_DOUBLE,
            padding=(0, 1),
        )

    # ─── SYS DISPATCH（工具调用面板）───────────────────────────────────────

    def _panel_dispatch(self) -> Panel:
        tbl = Table(box=None, show_header=False, padding=(0, 1),
                    expand=True, show_edge=False)
        tbl.add_column(style=_C_PRIMARY, width=3,  no_wrap=True)
        tbl.add_column(style=_C_ACCENT,  width=22, overflow="fold")
        tbl.add_column(style=_C_DIM,     width=6,  justify="right")
        tbl.add_column(no_wrap=True)

        with self._lock:
            tools = list(self._tool_log)

        # 计算最大 ms 用于归一化条形
        max_ms = max((t[1] for t in tools), default=1) or 1

        for name, ms, group, ts in tools[-10:]:
            icon   = _tool_icon(name)
            bar    = _bar(ms / max_ms, width=10)
            ms_str = f"{ms}ms" if ms < 1000 else f"{ms/1000:.1f}s"
            bar_colored = (
                f"[{_C_ACTIVE}]{bar[:int(ms/max_ms*10)]}[/{_C_ACTIVE}]"
                f"[{_C_DIM}]{bar[int(ms/max_ms*10):]}[/{_C_DIM}]"
            )
            tbl.add_row(icon, name[:20], ms_str,
                        Text.from_markup(bar_colored))

        if not tools:
            tbl.add_row("◌", "── 等待部署 ──", "", "")

        # 标题带实时计数
        with self._lock:
            total_tools = self._stats["tools"]

        return Panel(
            tbl,
            title=(
                f"[bold {_C_PRIMARY}]◈ SYS DISPATCH[/bold {_C_PRIMARY}]"
                f"  [{_C_METRIC}]{total_tools} CALLS[/{_C_METRIC}]"
            ),
            border_style=_C_PRIMARY,
            box=_DOUBLE,
            padding=(0, 1),
        )

    # ─── INTEL BOARD（情报看板面板）─────────────────────────────────────────

    def _panel_intel(self) -> Panel:
        with self._lock:
            st      = self._stats.copy()
            hits    = dict(self._group_hits)
            active  = len(self._active)

        avg_ms = (st["total_ms"] // st["turns"]) if st["turns"] else 0

        # ── 关键指标 ──
        metric_tbl = Table(box=None, show_header=False, padding=(0, 1),
                           expand=True, show_edge=False)
        metric_tbl.add_column(style=_C_DIM,    width=9, no_wrap=True)
        metric_tbl.add_column(style=_C_METRIC, justify="right")

        rows = [
            ("MESSAGES", str(st["msg"])),
            ("TOOL CALLS", str(st["tools"])),
            ("TOKENS",    f"{st['tokens']:,}"),
            ("AVG RT",    f"{avg_ms}ms"),
            ("ERRORS",    f"[{_C_ERROR if st['errors'] else _C_DIM}]{st['errors']}[/{'bright_red' if st['errors'] else 'dim'}]"),
        ]
        for label, val in rows:
            metric_tbl.add_row(label, val)

        # ── 群活跃度热力图 ──
        heat_lines: list[str] = []
        if hits:
            max_hit = max(hits.values()) or 1
            heat_lines.append(
                f"\n  [{_C_DIM}]── GROUP HEATMAP ──[/{_C_DIM}]"
            )
            for grp, cnt in sorted(hits.items(), key=lambda x: -x[1])[:5]:
                bar   = _bar(cnt / max_hit, width=8)
                b_col = (
                    f"[{_C_ACTIVE}]{bar[:int(cnt/max_hit*8)]}[/{_C_ACTIVE}]"
                    f"[{_C_DIM}]{bar[int(cnt/max_hit*8):]}[/{_C_DIM}]"
                )
                heat_lines.append(
                    f"  [{_C_ACCENT}]{grp[:9]:<9}[/{_C_ACCENT}] "
                    f"{Text.from_markup(b_col).__str__()}"
                    f" [{_C_DIM}]{cnt}[/{_C_DIM}]"
                )

        # ── 系统能力 badge ──
        badges = (
            f"\n  [{_C_DIM}]── CAPABILITIES ──[/{_C_DIM}]\n"
            f"  [{_C_ACTIVE}]✓[/{_C_ACTIVE}] [{_C_DIM}]ADAPTIVE THINK[/{_C_DIM}]\n"
            f"  [{_C_ACTIVE}]✓[/{_C_ACTIVE}] [{_C_DIM}]PROMPT CACHING[/{_C_DIM}]\n"
            f"  [{_C_ACTIVE}]✓[/{_C_ACTIVE}] [{_C_DIM}]CROSS-GRP LEARN[/{_C_DIM}]\n"
            f"  [{_C_ACTIVE}]✓[/{_C_ACTIVE}] [{_C_DIM}]MEMORY LAYER×3[/{_C_DIM}]"
        )

        parts: list = [metric_tbl]
        if heat_lines:
            parts.append(Text.from_markup("\n".join(heat_lines)))
        parts.append(Text.from_markup(badges))

        return Panel(
            RGroup(*parts),
            title=f"[bold {_C_PRIMARY}]◈ INTEL BOARD[/bold {_C_PRIMARY}]",
            border_style=_C_PRIMARY,
            box=_DOUBLE,
            padding=(0, 1),
        )

    # ─── 底部状态栏 ──────────────────────────────────────────────────────────

    def _panel_footer(self) -> Panel:
        pulse = _PULSE[self._frame % len(_PULSE)]
        items = [
            f"[{_C_PRIMARY}]{pulse}[/{_C_PRIMARY}]",
            f"[{_C_DIM}]SFG-AGENT v{self._version}[/{_C_DIM}]",
            f"[{_C_PRIMARY}]◈[/{_C_PRIMARY}] [{_C_ACTIVE}]ONLINE[/{_C_ACTIVE}]",
            f"[{_C_PRIMARY}]◈[/{_C_PRIMARY}] [{_C_DIM}]ADAPTIVE-THINKING[/{_C_DIM}]",
            f"[{_C_PRIMARY}]◈[/{_C_PRIMARY}] [{_C_DIM}]PROMPT-CACHING[/{_C_DIM}]",
            f"[{_C_PRIMARY}]◈[/{_C_PRIMARY}] [{_C_DIM}]CROSS-GROUP-LEARNING[/{_C_DIM}]",
            f"[{_C_DIM}]Press Ctrl+C to exit TUI[/{_C_DIM}]",
        ]
        return Panel(
            Align.center(Text.from_markup("  ".join(items))),
            border_style=_C_DIM,
            padding=(0, 1),
        )


# ─── 工具调用 (legacy) ────────────────────────────────────────────────────────

_SPINNER_FRAMES = _SPINCHAR


class KawaiiSpinner:
    """轻量级终端 Spinner，兼容非 TTY 环境。"""

    def __init__(self, message: str = "思考中…"):
        self._msg = message
        self._frame_idx = 0
        self._start = time.monotonic()
        self._active = False

    def start(self) -> None:
        self._active = True
        self._start = time.monotonic()
        if _RICH_AVAILABLE and console and console.is_terminal:
            console.print(f"  {_SPINNER_FRAMES[0]} {self._msg}", end="\r", highlight=False)

    def tick(self) -> None:
        if not self._active:
            return
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        elapsed = time.monotonic() - self._start
        if _RICH_AVAILABLE and console and console.is_terminal:
            console.print(
                f"  {_SPINNER_FRAMES[self._frame_idx]} {self._msg} ({elapsed:.1f}s)",
                end="\r", highlight=False,
            )

    def stop(self, final_msg: str = "") -> None:
        self._active = False
        if _RICH_AVAILABLE and console and console.is_terminal:
            console.print(" " * 60, end="\r")
        if final_msg:
            print_line(final_msg)

    def print_above(self, text: str) -> None:
        if _RICH_AVAILABLE and console and console.is_terminal:
            console.print(" " * 60, end="\r")
        print_line(text)
        if self._active and _RICH_AVAILABLE and console and console.is_terminal:
            elapsed = time.monotonic() - self._start
            console.print(
                f"  {_SPINNER_FRAMES[self._frame_idx]} {self._msg} ({elapsed:.1f}s)",
                end="\r", highlight=False,
            )


def format_tool_call(tool_name: str, elapsed_ms: int, extra: str = "") -> str:
    icon = _tool_icon(tool_name)
    name_part  = tool_name[:22].ljust(24)
    extra_part = (extra[:14].ljust(16)) if extra else " " * 16
    elapsed_s  = f"{elapsed_ms / 1000:.1f}s"
    return f"  ┊ {icon} {name_part}{extra_part}{elapsed_s}"


def print_tool_call(tool_name: str, elapsed_ms: int, extra: str = "") -> None:
    print_line(format_tool_call(tool_name, elapsed_ms, extra))


def print_line(text: str) -> None:
    if _RICH_AVAILABLE and console:
        console.print(text, highlight=False)
    else:
        print(text)


def print_thinking_block(thinking_text: str, max_chars: int = 400) -> None:
    if not thinking_text:
        return
    snippet = thinking_text[:max_chars].strip()
    if len(thinking_text) > max_chars:
        snippet += "…"
    if _RICH_AVAILABLE and console:
        console.print(Panel(snippet,
            title=f"[bold {_C_METRIC}]◈ NEURAL THINKING[/bold {_C_METRIC}]",
            border_style=_C_METRIC, padding=(0, 1),
        ))
    else:
        print(f"[thinking] {snippet}")


# ─── 启动横幅 ─────────────────────────────────────────────────────────────────

_LOGO_ASCII = """\
 ▄████████╗ ███████╗ ██████╗      ███╗  ██╗ ██████╗ ██╗   ██╗ ██████╗  █████╗ ██╗
 ╚══╬════╝ ██╔════╝ ██╔════╝      ████╗ ██║ ██╔══╝  ██║   ██║ ██╔══██╗██╔══██╗██║
    ║       █████╗  ██║  ███╗     ██╔█████║ █████╗  ██║   ██║ ██████╔╝███████║██║
    ║       ██╔══╝  ██║   ██║     ██║╚████║ ██╔══╝  ██║   ██║ ██╔══██╗██╔══██║██║
    ║       ██║     ╚██████╔╝     ██║ ╚███║ ███████╗╚██████╔╝ ██║  ██║██║  ██║███████╗
    ╚       ╚═╝      ╚═════╝      ╚═╝  ╚══╝ ╚══════╝ ╚═════╝  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝"""


def build_welcome_banner(
    model: str,
    tool_count: int,
    whitelist_count: int,
    version: str = "v1.0",
) -> None:
    if not (_RICH_AVAILABLE and console):
        print(f"\n=== SFG_AGENT {version} | {model} | {tool_count} tools ===\n")
        return

    info = Table.grid(padding=(0, 2))
    info.add_column(style=_C_DIM)
    info.add_column(style=f"bold {_C_ACTIVE}")

    info.add_row("VERSION", version)
    info.add_row("MODEL",   model)
    info.add_row("TOOLS",   str(tool_count))
    info.add_row("USERS",   str(whitelist_count))
    info.add_row("THINK",   "ADAPTIVE ✓")
    info.add_row("CACHE",   "EPHEMERAL ✓")

    grid = Table.grid(padding=(0, 3))
    grid.add_column()
    grid.add_column()
    grid.add_row(Text.from_markup(f"[bold {_C_PRIMARY}]{_LOGO_ASCII}[/bold {_C_PRIMARY}]"), info)

    console.print(Panel(
        grid,
        title=f"[bold {_C_PRIMARY}]◈ SFG AGENT NEURAL COMMAND MATRIX ◈[/bold {_C_PRIMARY}]",
        subtitle=f"[{_C_DIM}]飞书多群游戏运营 AI · Powered by Claude[/{_C_DIM}]",
        border_style=_C_PRIMARY,
        box=_DOUBLE,
        padding=(1, 3),
    ))
    console.print()


def print_startup_step(label: str, status: str = "ok", detail: str = "") -> None:
    icon  = {"ok": "✓", "warn": "⚠", "error": "✕", "skip": "·"}.get(status, "·")
    color = {"ok": _C_ACTIVE, "warn": _C_METRIC, "error": _C_ERROR, "skip": _C_DIM}.get(status, _C_DIM)
    line  = f"  [{color}]{icon}[/{color}]  [{_C_DIM}]{label:<20}[/{_C_DIM}]"
    if detail:
        line += f"  [{_C_ACCENT}]{detail}[/{_C_ACCENT}]"
    if _RICH_AVAILABLE and console:
        console.print(line)
    else:
        print(f"  {icon}  {label}  {detail}")


def print_separator() -> None:
    if _RICH_AVAILABLE and console:
        console.rule(style=_C_DIM)
    else:
        print("─" * 60)
