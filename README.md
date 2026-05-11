# SFG_AGENT

<div align="center">

**TeamAgent AI 游戏运营助手 · 越用越聪明**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Claude](https://img.shields.io/badge/Claude-Adaptive_Thinking-blueviolet?logo=anthropic)](https://anthropic.com)
[![License](https://img.shields.io/badge/License-MIT-22c55e)](LICENSE)
[![Self-Learning](https://img.shields.io/badge/Self--Learning-Federated-orange)](docs/)

*一个真正为游戏运营团队设计的 AI 助手：多飞书群协作、多级记忆、联合自学习——你们用得越多，它懂你们越深。*

</div>

---

```
你：@Bot 帮我把1区今天异常充值的玩家封禁，发邮件告知原因

Bot 思考 2.3s...
Bot：[list_zones → search_players → batch_send_gm × 3 → send_message]
     ✓ 发现 3 名异常玩家，已执行封禁
     ✓ 已向运营群发送处理报告：https://feishu.cn/...
     → 记忆：此类操作流程已记录，下次可以直接说"封禁异常充值"
```

---

## 它和其他 Feishu Bot 有什么不同？

大多数飞书机器人是**无记忆的命令执行器**——每次对话都是全新的，不知道你上周处理了什么，不知道哪个区服最近不稳定，不知道你们团队的操作习惯。

SFG_AGENT 的设计出发点是：**一个真正的运营同事，而不是一个聪明的搜索框。**

| 能力 | 普通飞书 Bot | SFG_AGENT |
|------|------------|-----------|
| 记住上下文 | 单次对话 | 跨重启持久化（三层命名空间）|
| 学习团队习惯 | ✗ | ✓ 自动提炼重复操作为技能 |
| 多群协作学习 | ✗ | ✓ 跨群热力图，真正通用规律才学 |
| 添加新工具 | 改代码 + 重部署 | 向导脚手架一键生成 + 2 行配置 |
| 推理能力 | 固定逻辑 | Adaptive Thinking，复杂任务自动升级 |
| Token 成本 | — | Prompt Caching，重复系统提示节省 90% |
| 并发工具调用 | — | asyncio 并发 + Minion 确定性队列 |
| 运行监控 | — | 神经指挥矩阵全屏 TUI 实时看板 |

---

## 核心特性

### 🧠 三层记忆 · 跨重启持久化

```
global://  → "批量 GM 指令的正确格式是…"（所有群共享）
group://   → "这个群的运营习惯是每周一发周报"（群级）
user://    → "小王的常用区服是 3、7、12 区"（个人级）
```

每次对话前自动预取三层记忆，RRF 加权融合（user×3 > group×2 > global×1），优先返回最个性化的知识。

### 🌐 跨群联合学习

这是 SFG_AGENT 区别于所有同类项目的核心设计：

```
群A：[list_zones + send_gm] 出现 5 次
群B：[list_zones + send_gm] 出现 7 次
群C：[list_zones + send_gm] 出现 4 次
                ↓  凌晨 Dream 周期
   → 这是跨 3 个群的通用操作流程
   → 自动生成操作手册写入全局技能库
   → 所有群的 Bot 都受益
```

单群偶发操作不会触发学习（避免噪音），只有**在多个群中真实反复出现的模式**才被提炼为通用技能。

### 🔌 插件式工具扩展

接入新工具只需两步，无需改任何框架代码：

```python
# 1. 在 tools/wiki_tools.py 中定义
PROVIDER_META = {
    "register_fn": "register_wiki_tools",
    "domain_keywords": ["知识库", "wiki", "文档搜索"],
    "tool_name_prefixes": ["wiki_"],
}

def register_wiki_tools(registry):
    @registry.register(name="wiki_search", ...)
    async def wiki_search(query: str): ...
```

```yaml
# 2. 在 config.yaml 中加一行
tool_providers:
  - module: tools.wiki_tools
    enabled: true
```

重启后，TeamManager 自动学会"知识库"类消息路由到新工具，无需任何额外配置。

### 💭 Adaptive Thinking · 按需付费

```
简单查询  → effort: low   → 响应最快，几乎无额外推理成本
日常操作  → effort: medium → 均衡，自动处理大多数场景
复杂分析  → effort: high   → 深度推理，多步规划
/think max → effort: max   → 无约束，处理最棘手的问题
```

多轮工具调用或连续出错时**自动升级** effort，用户手动设置的不被覆盖。

### ⚡ 零 Token 路由

```
@Bot 帮我查一下1区的在线人数
     ↓ TeamManager 关键词匹配（0 LLM 调用）
     → 路由到 game 域，仅传入 game 工具定义
     → 减少 ~60% 工具描述 token
```

多域混合任务自动退回全工具集，安全保底不误伤。

### 🖥️ 神经指挥矩阵 · 全屏 TUI 看板

运行 `python main.py tui` 进入全屏科技风实时看板：

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  ◈ SFG NEURAL COMMAND MATRIX   ◐  SCANNING   REQ:42  ERR:0     ┃
┃  claude-sonnet-4-6 / v1.0 / uptime 03:42:17                    ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃ ◈ COMM SIGNALS        ┃ ◈ SYS DISPATCH         ┃ ◈ INTEL BOARD ┃
┃  ⠋ 运营群 / 小明      ┃  feishu_send  ██████░  ┃  requests  42 ┃
┃    封禁异常充值玩家    ┃  list_zones   ████░░░  ┃  tokens   18k ┃
┃  ✓ 技术群 / 小红      ┃  send_gm      ███░░░░  ┃  p50     1.2s ┃
┃    1区在线人数         ┃                        ┃  p99     4.7s ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃  █ PULSE ▓▒░  ONLINE  ●  TOOLS 12  ●  SKILLS 8  ●  MEMORY ON  ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

三栏布局：
- **COMM SIGNALS** — 实时请求流（转圈 → 完成 ✓ / 报错 ✕）
- **SYS DISPATCH** — 工具调用时序热力条形图
- **INTEL BOARD** — 系统指标、群活跃度热力图、能力徽章

普通 daemon 模式（`python main.py start`）在有 TTY 时展示紧凑版看板，无 TTY 时自动禁用。

---

## 架构总览

```
飞书群消息 (@Bot)
        │
        ▼
  [Gateway] lark-cli event subscribe
        │
        ▼
  [Handlers] 权限验证 · Slash 命令 · SendPolicy 规则引擎
        │
        ▼
  ┌─────────────────────────────────────────────────────┐
  │              Orchestrator Agent Loop                │
  │                                                     │
  │  TeamManager ── 关键词路由 ──► 按域过滤工具集          │
  │  PromptBuilder ─ Prompt Caching ─ 平台感知 Prompt    │
  │  ThinkingManager ─ Adaptive Thinking (off→max)      │
  │  MemoryManager ── 三层预取 + GM失败归因写入            │
  │  ContextEngine ── 75%触发压缩 + 操作安全窗口           │
  │  AgentDashboard ─ 实时 TUI 看板（Rich Live）          │
  │                                                     │
  │  ┌─ tool_use loop (max 20轮) ──────────────────┐    │
  │  │  并发执行 · Preflight 注入检测 · Jitter 重试  │    │
  │  └──────────────────────────────────────────────┘    │
  └──────────────┬──────────────────────────────────────┘
                 │ 异步（不阻塞回复）
                 ▼
  ┌──────────────────────────────────────────────────────┐
  │                  自学习引擎                           │
  │  Skillify ─ 单群热力图（≥3次触发）                    │
  │           ─ 跨群热力图写入 GroupHeatmap               │
  │  Dream    ─ 凌晨7阶段维护 + federated_learn           │
  │  Curator  ─ 闲置2h触发语义合并/归档                   │
  └──────────────────────────────────────────────────────┘
                 │
                 ▼
  ~/.claude/skills/  →  下次对话自动加载最新技能
```

---

## 快速开始

### 前置条件

```bash
# Python 3.11+（推荐 uv）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 飞书 CLI
npm install -g @larksuiteoapi/lark-cli
```

### 5 分钟安装（交互式向导）

```bash
# 1. 获取代码
git clone https://github.com/yourname/sfg_agent.git && cd sfg_agent

# 2. 启动交互式向导（可选择性跳过任意步骤）
python setup_agent.py wizard
```

向导菜单：
```
╔══════════════════════════════════════════════════════╗
║        SFG_AGENT 初始化向导  v1.0.0                  ║
╚══════════════════════════════════════════════════════╝

  [1] 安装 Python 依赖
  [2] 配置 API Key / 飞书 Bot
  [3] 配置 GM 服务器          (可选 · 来自 game_tools)
  [4] 飞书 Bot 登录           (可选 · 来自 feishu_tools)
  [5] 环境诊断
  [6] 连接测试

  [a] 全部执行    [q] 退出
```

每个步骤可单独运行或跳过；工具模块（`tools/*.py`）中的 `SETUP_META` 自动被发现并加入菜单。

### 最小配置

只需填写 5 项即可运行：

```yaml
anthropic:
  api_key: "${ANTHROPIC_AUTH_TOKEN}"
  base_url: "${ANTHROPIC_BASE_URL}"
  model: "${ANTHROPIC_MODEL}"         # 如 claude-sonnet-4-6

lark:
  bot_open_id: "ou_xxx"               # lark-cli whoami --as bot

permissions:
  whitelist:
    - open_id: "ou_你的open_id"
      name: "你的名字"
      roles: ["admin"]
```

---

## 使用示例

```
# 游戏运维
@Bot 查一下所有区服的在线人数，哪个最低
@Bot 把测试服角色"剑士888"复制到1区，并发邮件通知玩家
@Bot uid=12345 最近三天有没有可疑充值行为

# 飞书操作
@Bot 给"运营日报"群发今日收入汇总，记得 @产品总监
@Bot 帮我搜一下"发版流程"文档，整理成要点

# 记忆与学习
@Bot 记住：每周五下午3点有运营周会，提前半小时提醒我
@Bot 上次那个批量封禁脚本怎么写的？
@Bot 1区的技术对接人是谁？

# 复杂分析
/think high
@Bot 分析上个月所有区服的付费数据，找出异常波动的区服和可能原因
```

---

## 命令参考

### main.py

| 命令 | 说明 |
|------|------|
| `python main.py start` | 后台 daemon 启动 |
| `python main.py tui` | 前台全屏神经指挥矩阵 TUI 模式 |
| `python main.py stop` | 停止 daemon |
| `python main.py restart` | 重启 daemon |
| `python main.py status` | 查看运行状态 |
| `python main.py logs` | 实时日志（tail -f）|

### setup_agent.py

| 命令 | 说明 |
|------|------|
| `python setup_agent.py wizard` | 交互式初始化向导（推荐首次使用）|
| `python setup_agent.py install` | 安装 Python 依赖 |
| `python setup_agent.py config` | 配置 API Key / 飞书 Bot |
| `python setup_agent.py lark` | 飞书 Bot 登录（lark-cli auth login）|
| `python setup_agent.py gm` | 配置 GM 服务器连接 |
| `python setup_agent.py doctor` | 环境诊断 |
| `python setup_agent.py check` | API + 飞书连接测试 |
| `python setup_agent.py new-tool` | 脚手架：生成新工具模板 |

### 群内斜杠命令

| 命令 | 说明 |
|------|------|
| `/clear` | 清除当前对话历史 |
| `/think [off\|low\|medium\|high\|max]` | 调整推理深度 |
| `/compact` | 手动压缩历史（减少 token）|
| `/stats` | 对话统计（token / 工具调用 / 耗时）|
| `/help` | 帮助 |

---

## 权限模型

```yaml
roles:
  admin:
    allowed_tools: ["*"]               # 全部功能
  operator:
    allowed_tools: ["feishu_tools", "game_tools.list_zones", "game_tools.send_gm_command"]
    denied_tools:  ["game_tools.copy_role", "game_tools.batch_send_gm_command"]
  viewer:
    allowed_tools: ["feishu_tools", "game_tools.list_zones"]
```

角色在 `config.yaml` 的 `whitelist` 中按人配置，修改后无需重启立即生效。

---

## 扩展开发

### 添加新工具（脚手架）

```bash
python setup_agent.py new-tool
```

交互式选择模板类型：

| 模板 | 适用场景 |
|------|---------|
| `http` | 调用 REST API（如内部数据平台）|
| `cli` | 包装命令行工具（如 lark-cli、git）|
| `mcp` | 对接 MCP stdio 服务（如 gm_server）|
| `skill` | 创建 SKILL.md 知识技能文件 |

生成的工具文件自动包含 `PROVIDER_META`（路由元数据）和 `SETUP_META`（向导步骤），直接在 `config.yaml` 加一行 `tool_providers` 条目后重启即可生效。

### 手动添加工具

```python
# tools/your_tool.py

PROVIDER_META = {
    "name": "your_tools",
    "description": "你的工具描述",
    "register_fn": "register_your_tools",
    "domain_name": "your_domain",       # TeamManager 路由域
    "domain_keywords": ["关键词1", "关键词2"],
    "tool_name_prefixes": ["your_"],
    "weight": 2,
}

# 可选：向导自动发现
SETUP_META = {
    "step_name": "配置你的工具",
    "description": "交互式配置步骤说明",
    "optional": True,
    "setup_fn": "setup_step",
}

def setup_step() -> bool:
    # 仅用 stdlib，安装依赖前也能运行
    ...

def register_your_tools(registry):
    @registry.register(
        name="your_action",
        description="做某件事",
        input_schema={"type": "object", "properties": {"param": {"type": "string"}}},
    )
    async def your_action(param: str):
        return {"result": param}
```

在 `config.yaml` 加一行后重启，所有群的 Bot 立即获得新能力，向导菜单也自动出现新步骤。

### 自学习技能格式

自动写入 `~/.claude/skills/<category>/SKILL.md`，所有对话自动加载最新技能：

```markdown
## 自动学习 2026-05-10 03:15 · 跨群聚合（5群，47次触发）

标题：批量封禁异常玩家
场景：发现多个玩家在短时间内出现异常充值行为
步骤：
  1. 调用 search_players 筛选异常玩家列表
  2. 确认玩家 uid 和操作原因
  3. 调用 batch_send_gm 执行封禁
  4. 调用 send_message 向运营群发送处理报告
注意：批量操作需要 admin 角色权限；操作前必须人工确认 uid 列表
```

---

## 技术设计亮点

### 操作安全窗口
压缩前检测是否有未配对的 `tool_use`。GM 指令执行链中途**禁止压缩**，防止"刚才确认要操作哪个区服"的上下文被摘要掉。

### GM 失败归因记忆
工具调用失败时自动提炼：失败原因 + 正确做法，写入 `error_pattern` 类型记忆。下次执行同类操作，主动返回"上次这个指令失败过，原因是…"。

### Prompt Caching
系统提示的静态部分（身份/平台规则/已学习技能）加 `cache_control: ephemeral`，在高频群聊场景下，每次对话节省约 90% 的系统提示 token 成本。

### 跨群联合学习飞轮
```
更多群使用 → 更多工具 combo 数据 → 更高质量的跨群技能
更好的技能 → 更精准的操作建议 → 吸引更多群使用
```

---

## 项目结构

```
sfg_agent/
├── main.py                  # CLI（start/stop/restart/status/logs/tui）
├── setup_agent.py           # 向导（wizard/install/config/lark/gm/doctor/check/new-tool）
├── config.example.yaml      # 配置模板
│
├── agent/
│   ├── orchestrator.py      # Agent Loop（Hermes+GBrain+OpenClaw 融合）
│   ├── thinking.py          # Adaptive Thinking (off/low/medium/high/max)
│   ├── team_manager.py      # 零 Token 关键词路由
│   ├── prompt_builder.py    # 平台感知 Prompt + Prompt Caching
│   ├── display.py           # 神经指挥矩阵 TUI（Rich Live 全屏/紧凑双模式）
│   ├── memory_manager.py    # 三层记忆预取 + 失败归因写入
│   └── context_engine.py    # 轨迹压缩 + 操作安全窗口
│
├── tools/
│   ├── registry.py          # 插件式 Provider（PROVIDER_META + SETUP_META 驱动）
│   ├── feishu_tools.py      # 飞书消息/群/用户/文档
│   ├── game_tools.py        # GM 指令/区服/角色复制（HTTP direct）
│   ├── memory_tools.py      # remember / recall / forget
│   └── minion_tools.py      # 确定性任务（ping_zone 等）
│
├── storage/
│   ├── session_store.py     # SQLite WAL 会话历史（TTL 7天）
│   ├── memory_store.py      # FTS5 trigram + 可选向量检索
│   ├── group_heatmap.py     # 跨群工具热力图（联合学习数据源）
│   └── minion_queue.py      # asyncio 确定性任务队列
│
└── skills/
    ├── skillify.py          # 单群+跨群双通道学习
    ├── dream.py             # 夜间7阶段维护 + 联合学习
    └── curator.py           # 技能库语义合并/归档
```

---

## 版本历史

### v1.0（当前）
- **神经指挥矩阵 TUI**：`python main.py tui` 全屏科技风看板（COMM/DISPATCH/INTEL 三栏）
- **交互式设置向导**：`python setup_agent.py wizard`，工具模块通过 `SETUP_META` 自动注册步骤
- **新工具脚手架**：`python setup_agent.py new-tool`，4 种模板（HTTP / CLI / MCP / SKILL）
- **跨群联合学习**：GroupHeatmap + Dream federated_learn 阶段
- **插件式 Tool Provider**：PROVIDER_META 自描述 + 签名自动注入
- **Adaptive Thinking**：升级到 `effort` 参数，废弃 `budget_tokens`
- **Prompt Caching**：静态系统提示加 `cache_control`，高频群聊节省 ~90% token
- **TeamManager**：关键词路由从 provider 动态加载，新工具自动加入路由表

### v2.0
- 融合 Hermes + GBrain + OpenClaw 三框架架构
- 操作安全窗口 · 三层记忆命名空间 · GM 失败归因 · 工具热力图 Skillify
- token 触发轨迹压缩 · Decorrelated Jitter 重试 · 错误分类器

### v1.0
- 基础飞书 Bot · SQLite 会话 · 白名单权限

---

## 故障排查速查

```bash
# 环境诊断（推荐先跑这个）
python setup_agent.py doctor

# API + 飞书连通性测试
python setup_agent.py check

# 实时日志
python main.py logs

# 全屏 TUI 监控模式
python main.py tui

# Bot 不回复 → 检查飞书登录
lark-cli whoami --as bot
python setup_agent.py lark   # 重新登录

# GM 指令失败 → 查审计日志
tail -50 ~/sfg_agent/data/audit.log

# Token 超限 → 手动压缩
# 在飞书群发：/compact
```

---

## License

[MIT](LICENSE) © 2026 SFG_AGENT Contributors

---

<div align="center">

*每一次 @Bot，都是在让它变得更懂你们的项目。*

</div>
