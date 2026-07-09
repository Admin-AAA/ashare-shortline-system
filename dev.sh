#!/usr/bin/env bash
# 开发环境服务管理脚本
# 前端 5174 / 后端 8765 由 launchd 托管（开机自启 + 崩溃自重启）。
# 本脚本只做「重启 / 状态 / 停止 / 日志」——通过 launchctl 操作，状态和自启配置完全一致。
set -euo pipefail

UID_NUM="$(/usr/bin/id -u)"
FRONTEND_LABEL="com.wangao.ashare-frontend"
BACKEND_LABEL="com.wangao.ashare-backend"
FRONTEND_PORT=5174
BACKEND_PORT=8765
FRONTEND_LOG="$HOME/Library/Logs/ashare-frontend.err.log"
BACKEND_LOG="$HOME/Library/Logs/ashare-backend.err.log"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[dev]${NC} $*"; }
info() { echo -e "${CYAN}[dev]${NC} $*"; }
warn() { echo -e "${YELLOW}[dev]${NC} $*"; }
err()  { echo -e "${RED}[dev]${NC} $*" >&2; }

port_pid() { lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null || true; }

status_one() {
  local label=$1 port=$2 name=$3
  local loaded pid http
  if launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
    loaded="已托管"
  else
    loaded="未加载"
  fi
  pid="$(port_pid "$port")"
  if [ -n "$pid" ]; then
    if curl -sS -o /dev/null --max-time 3 "http://127.0.0.1:$port/" 2>/dev/null; then
      http="HTTP 200 ✓"
    else
      http="端口占用但无响应"
    fi
  else
    http="未监听"
  fi
  printf "  %-6s :%-5s  进程=%-7s  %-12s  %s\n" "$name" "$port" "${pid:-无}" "$loaded" "$http"
}

cmd_status() {
  log "服务状态："
  status_one "$FRONTEND_LABEL" "$FRONTEND_PORT" "前端"
  status_one "$BACKEND_LABEL"  "$BACKEND_PORT"  "后端"
}

kick() {  # 重启单个 launchd 服务
  local label=$1 name=$2 port=$3
  if ! launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
    err "$name 未加载 launchd 配置，跳过。先 bootstrap：launchctl bootstrap gui/$UID_NUM ~/Library/LaunchAgents/${label}.plist"
    return 1
  fi
  info "重启 $name ($label) ..."
  launchctl kickstart -k "gui/$UID_NUM/$label"
  sleep 3
  local pid
  pid="$(port_pid "$port")"
  if [ -n "$pid" ]; then
    log "$name 已就绪  http://127.0.0.1:$port  (PID $pid)"
  else
    err "$name 启动失败，看日志：tail -f $HOME/Library/Logs/ashare-${name}.err.log"
    return 1
  fi
}

cmd_restart() {
  local ok=0
  if [ "${1:-all}" = "frontend" ] || [ "${1:-all}" = "all" ]; then kick "$FRONTEND_LABEL" 前端 "$FRONTEND_PORT" || ok=1; fi
  if [ "${1:-all}" = "backend"  ] || [ "${1:-all}" = "all" ]; then kick "$BACKEND_LABEL"  后端 "$BACKEND_PORT"  || ok=1; fi
  [ $ok -eq 0 ] && log "重启完成" || exit 1
}

cmd_stop() {
  local label=$1 name=$2
  launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1 || { warn "$name 未加载，无需停止"; return; }
  # bootout 会卸载配置（下次不再自启）；如只想临时停、保留开机自启，改用 kill
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  warn "$name 已停止并取消 launchd 托管（重新启动：./dev.sh start $name）"
}

cmd_start() {
  local label=$1 name=$2 plist
  plist="$HOME/Library/LaunchAgents/${label}.plist"
  [ -f "$plist" ] || { err "找不到 $plist"; exit 1; }
  launchctl bootstrap "gui/$UID_NUM" "$plist" 2>/dev/null \
    || launchctl load "$plist" 2>/dev/null \
    || true
  launchctl enable "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl kickstart -k "gui/$UID_NUM/$label" 2>/dev/null || true
  sleep 3
  local port
  [ "$label" = "$FRONTEND_LABEL" ] && port=$FRONTEND_PORT || port=$BACKEND_PORT
  local pid; pid="$(port_pid "$port")"
  [ -n "$pid" ] && log "$name 已启动 (PID $pid) http://127.0.0.1:$port" || err "$name 启动失败，看日志"
}

cmd_logs() {
  case "${1:-}" in
    f|frontend) tail -n 50 -f "$FRONTEND_LOG";;
    b|backend)  tail -n 50 -f "$BACKEND_LOG";;
    *) echo "用法：./dev.sh logs [frontend|backend]";;
  esac
}

usage() {
  cat <<'EOF'
用法：./dev.sh <命令> [目标]
命令：
  status              查看前端/后端运行状态
  restart [frontend|backend|all]   重启服务（默认 all）   ← 你最常用的
  start   <frontend|backend>      重新托管并启动（stop 之后用）
  stop    <frontend|backend>      停止并取消 launchd 托管
  logs    [frontend|backend]      跟踪日志（Ctrl-C 退出）
不带参数默认 status。
EOF
}

CMD="${1:-status}"; shift || true
case "$CMD" in
  status)  cmd_status;;
  restart) cmd_restart "${1:-all}";;
  start)
    case "${1:-}" in
      f|frontend) cmd_start "$FRONTEND_LABEL" 前端;;
      b|backend)  cmd_start "$BACKEND_LABEL"  后端;;
      *) err "需要指定 frontend|backend"; exit 1;;
    esac;;
  stop)
    case "${1:-}" in
      f|frontend) cmd_stop "$FRONTEND_LABEL" 前端;;
      b|backend)  cmd_stop "$BACKEND_LABEL"  后端;;
      *) err "需要指定 frontend|backend"; exit 1;;
    esac;;
  logs)    cmd_logs "${1:-}";;
  -h|--help|help) usage;;
  *) err "未知命令：$CMD"; usage; exit 1;;
esac
