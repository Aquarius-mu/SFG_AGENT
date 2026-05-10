#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  SFG_AGENT 一键安装脚本
#  用法：bash install.sh
# ══════════════════════════════════════════════════════════
set -euo pipefail

AGENT_DIR="$HOME/sfg_agent"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }
step() { echo -e "\n${BOLD}── $* ──${NC}"; }

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════╗"
echo "  ║       SFG_AGENT Installer     ║"
echo "  ║  飞书游戏服务器 AI 助手        ║"
echo "  ╚═══════════════════════════════╝"
echo -e "${NC}"

# ──────────────────────────────────────────────────────────
# 1. 检查 uv（Python 包管理器）
# ──────────────────────────────────────────────────────────
step "检查 uv"
if ! command -v uv &>/dev/null; then
    if [[ -f "$HOME/.local/bin/uv" ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        warn "未找到 uv，正在安装..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
log "uv $(uv --version)"

# ──────────────────────────────────────────────────────────
# 2. 检查 Python 3.11+
# ──────────────────────────────────────────────────────────
step "检查 Python"
PYTHON=""
for candidate in \
    "$HOME/.local/share/uv/python/cpython-3.14.2-linux-x86_64-gnu/bin/python3.14" \
    "$HOME/.local/share/uv/python/cpython-3.13.*/bin/python3.13" \
    "$HOME/.local/share/uv/python/cpython-3.12.*/bin/python3.12" \
    "$HOME/.local/share/uv/python/cpython-3.11.*/bin/python3.11" \
    "$(which python3.14 2>/dev/null)" \
    "$(which python3.13 2>/dev/null)" \
    "$(which python3.12 2>/dev/null)" \
    "$(which python3.11 2>/dev/null)"; do
    # shellcheck disable=SC2086
    for p in $candidate; do
        if [[ -x "$p" ]]; then
            PYTHON="$p"
            break 2
        fi
    done
done

if [[ -z "$PYTHON" ]]; then
    warn "未找到 Python 3.11+，用 uv 安装 Python 3.12..."
    uv python install 3.12
    PYTHON=$(uv python find 3.12)
fi
log "Python: $PYTHON ($($PYTHON --version 2>&1))"

# ──────────────────────────────────────────────────────────
# 3. 检查 lark-cli
# ──────────────────────────────────────────────────────────
step "检查 lark-cli"
LARK_CLI=""
for p in \
    "$(which lark-cli 2>/dev/null)" \
    "$HOME/nodejs/node22.15/bin/lark-cli" \
    "$HOME/nodejs/node20/bin/lark-cli"; do
    if [[ -x "$p" ]]; then
        LARK_CLI="$p"
        break
    fi
done

if [[ -z "$LARK_CLI" ]]; then
    warn "未找到 lark-cli，请手动安装："
    warn "  npm install -g @larksuite/lark-cli"
    warn "  或：yarn global add @larksuite/lark-cli"
else
    log "lark-cli: $LARK_CLI"
fi

# ──────────────────────────────────────────────────────────
# 4. 创建虚拟环境
# ──────────────────────────────────────────────────────────
step "创建 Python 虚拟环境"
cd "$AGENT_DIR"
if [[ ! -d ".venv" ]]; then
    uv venv --python "$PYTHON" .venv
    log "虚拟环境创建完成：$AGENT_DIR/.venv"
else
    log "虚拟环境已存在，跳过"
fi

# ──────────────────────────────────────────────────────────
# 5. 安装依赖
# ──────────────────────────────────────────────────────────
step "安装 Python 依赖"
uv pip install -r requirements.txt --python .venv/bin/python
log "依赖安装完成"

# ──────────────────────────────────────────────────────────
# 6. 创建数据目录
# ──────────────────────────────────────────────────────────
step "初始化数据目录"
mkdir -p "$AGENT_DIR/data/cache"

# 兼容旧 bot：若 ~/feishu_bot/cache 存在，软链接
OLD_CACHE="$HOME/feishu_bot/cache"
NEW_CACHE="$AGENT_DIR/data/cache"
if [[ -d "$OLD_CACHE" && ! -L "$NEW_CACHE" ]]; then
    rm -rf "$NEW_CACHE"
    ln -sf "$OLD_CACHE" "$NEW_CACHE"
    log "已链接旧 bot 缓存：$OLD_CACHE"
fi
log "数据目录就绪：$AGENT_DIR/data/"

# ──────────────────────────────────────────────────────────
# 7. 生成启动脚本
# ──────────────────────────────────────────────────────────
step "生成启动脚本"
cat > "$AGENT_DIR/run.sh" <<EOF
#!/usr/bin/env bash
# SFG_AGENT 启动脚本（由 install.sh 自动生成）
LARK_CLI_PATH="$LARK_CLI"
export PATH="\$(dirname \$LARK_CLI_PATH):\$PATH"
cd "$AGENT_DIR"
source .venv/bin/activate
exec python main.py "\${1:-start}"
EOF
chmod +x "$AGENT_DIR/run.sh"
log "启动脚本：$AGENT_DIR/run.sh"

# ──────────────────────────────────────────────────────────
# 8. 写入 lark_cli_path 到配置（如果找到了）
# ──────────────────────────────────────────────────────────
if [[ -n "$LARK_CLI" && -f "$AGENT_DIR/config.yaml" ]]; then
    sed -i "s|lark_cli_path: \"\"|lark_cli_path: \"$LARK_CLI\"|g" "$AGENT_DIR/config.yaml" 2>/dev/null || true
fi

# ──────────────────────────────────────────────────────────
# 完成
# ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  安装完成！${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════${NC}"
echo ""

if [[ ! -f "$AGENT_DIR/config.yaml" ]]; then
    echo "下一步："
    echo "  1. 配置 Agent："
    echo -e "     ${BOLD}cd $AGENT_DIR && ./run.sh setup${NC}"
    echo "  2. 启动："
    echo -e "     ${BOLD}./run.sh start${NC}"
else
    echo "下一步："
    echo "  1. 确认飞书 Bot 已登录：lark-cli auth login --as bot"
    echo -e "  2. 启动 Agent：${BOLD}./run.sh start${NC}"
    echo -e "  3. 查看日志：${BOLD}./run.sh logs${NC}"
fi
echo ""
