#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# ── Colors ──
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[启动]${NC} $*"; }
warn() { echo -e "${YELLOW}[占用]${NC} $*"; }

# ── Port helper ──
free_port() {
  local port=$1
  local pid
  pid=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    warn "端口 $port 被 PID $pid 占用，正在关闭..."
    kill -9 $pid 2>/dev/null || true
    sleep 0.3
  fi
}

# ── Backend ──
free_port 8765
log "启动后端 (FastAPI on :8765)..."
cd "$BACKEND"

if [ ! -d ".venv" ]; then
  log "创建 Python 虚拟环境..."
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ ! -f ".venv/.deps_installed" ]; then
  log "安装 Python 依赖..."
  pip install -q -r requirements.txt
  touch .venv/.deps_installed
fi

uvicorn app.main:app --host 0.0.0.0 --port 8765 --reload &
BACKEND_PID=$!

# ── Frontend ──
free_port 5173
log "启动前端 (Vite on :5173)..."
cd "$FRONTEND"

if [ ! -d "node_modules" ]; then
  log "安装前端依赖..."
  npm install
fi

npx vite --host 0.0.0.0 &
FRONTEND_PID=$!

# ── Cleanup ──
cleanup() {
  log "正在关闭..."
  kill $BACKEND_PID 2>/dev/null || true
  kill $FRONTEND_PID 2>/dev/null || true
  wait 2>/dev/null
  log "已停止"
}
trap cleanup EXIT INT TERM

echo ""
log "====================================="
log "  后端:  http://localhost:8765"
log "  前端:  http://localhost:5173"
log "  API文档: http://localhost:8765/docs"
log "====================================="
echo ""

wait