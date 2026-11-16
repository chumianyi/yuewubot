#!/usr/bin/env bash
# YueWuBot 已部署后启动：直接拉起服务
# 不重跑安装、不装依赖、不重新生成配置，适合已经部署完成的机器日常使用
# 用法：./已部署后启动.sh            启动（已在运行则提示）
#       ./已部署后启动.sh restart    先停再启
#       ./已部署后启动.sh status     查看运行状态
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
[ -n "${APP_DIR:-}" ] || { echo "无法定位脚本目录"; exit 1; }
cd "$APP_DIR" || exit 1

NAME="yuewubot"
PORT="${YW_PORT:-6199}"
HOST="${YW_HOST:-127.0.0.1}"
VENV="$APP_DIR/.venv"
PY_BIN="$VENV/bin/python"
SERVICE="/etc/systemd/system/${NAME}.service"
LOGDIR="$APP_DIR/logs"
LOG="$LOGDIR/app.log"
PWDFILE="$APP_DIR/.pid"

if [ -t 1 ]; then
  C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_END="\033[0m"
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_END=""
fi
ok()   { printf "${C_OK}[OK]${C_END} %s\n" "$*"; }
warn() { printf "${C_WARN}[!]${C_END} %s\n" "$*"; }
err()  { printf "${C_ERR}[FAIL]${C_END} %s\n" "$*" >&2; }
dim()  { printf "${C_DIM}%s${C_END}\n" "$*"; }

have_systemd() {
  command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

pid_of() {
  local pid
  if [ -f "$PWDFILE" ]; then
    pid="$(cat "$PWDFILE" 2>/dev/null | tr -d ' ')"
    case "$pid" in
      ''|*[!0-9]*) ;;
      *) if kill -0 "$pid" 2>/dev/null; then printf '%s' "$pid"; return 0; fi ;;
    esac
  fi
  pid="$(pgrep -f "$APP_DIR/yuewubot.py" 2>/dev/null | head -n1)"
  [ -n "$pid" ] && { printf '%s' "$pid"; return 0; }
  return 1
}

lan_ip() {
  if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "本机IP"
  else
    hostname -I 2>/dev/null | awk '{print $1}' || echo "本机IP"
  fi
}

cfg_token() {
  [ -f "$APP_DIR/data/config.json" ] || return 0
  [ -x "$PY_BIN" ] || return 0
  "$PY_BIN" - "$APP_DIR/data/config.json" <<'PY' 2>/dev/null
import json, sys
try:
    print((json.load(open(sys.argv[1])).get("auth") or {}).get("token") or "")
except Exception:
    print("")
PY
}

banner() {
  local token
  token="$(cfg_token)"
  echo
  dim "控制台：http://127.0.0.1:$PORT"
  if [ "$HOST" = "0.0.0.0" ]; then
    dim "局域网：http://$(lan_ip):$PORT"
  fi
  if [ -n "$token" ]; then
    dim "令牌：  $token"
  else
    warn "尚未生成访问令牌，执行：./start.sh token --reset"
  fi
  dim "停止：./已部署后启动.sh stop    日志：./已部署后启动.sh logs"
  echo
}

need_deployed() {
  if [ ! -f "$APP_DIR/yuewubot.py" ]; then
    err "缺少 yuewubot.py，请在安装目录内执行"
    exit 1
  fi
  if [ ! -x "$PY_BIN" ]; then
    err "尚未部署：找不到 $VENV"
    dim "  首次使用请执行：./start.sh"
    exit 1
  fi
}

do_start() {
  need_deployed
  if have_systemd && [ -f "$SERVICE" ]; then
    if systemctl is-active --quiet "$NAME" 2>/dev/null; then
      ok "已在运行（systemd 服务：$NAME）"
      banner
      return 0
    fi
    systemctl start "$NAME" >/dev/null 2>&1
    sleep 2
    if systemctl is-active --quiet "$NAME" 2>/dev/null; then
      ok "已启动（systemd 服务：$NAME）"
      banner
      return 0
    fi
    err "启动失败，最近日志："
    journalctl -u "$NAME" -n 20 --no-pager 2>/dev/null || true
    exit 1
  fi

  local pid
  if pid="$(pid_of)"; then
    ok "已在运行（PID $pid）"
    banner
    return 0
  fi
  mkdir -p "$LOGDIR" 2>/dev/null || true
  if [ -f "$LOG" ]; then
    local sz
    sz="$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')"
    case "$sz" in ''|*[!0-9]*) sz=0 ;; esac
    [ "$sz" -gt 5242880 ] && mv -f "$LOG" "$LOG.1" 2>/dev/null
  fi
  nohup env YW_HOST="$HOST" YW_PORT="$PORT" PYTHONUNBUFFERED=1 \
    "$PY_BIN" "$APP_DIR/yuewubot.py" >>"$LOG" 2>&1 &
  pid=$!
  echo "$pid" > "$PWDFILE"
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    ok "已启动（PID $pid）"
    banner
    return 0
  fi
  err "启动失败，最近日志："
  tail -n 20 "$LOG" 2>/dev/null || true
  rm -f "$PWDFILE"
  exit 1
}

do_stop() {
  if have_systemd && [ -f "$SERVICE" ]; then
    systemctl stop "$NAME" >/dev/null 2>&1 || true
    ok "已停止（systemd）"
    rm -f "$PWDFILE"
    return 0
  fi
  local pid
  if pid="$(pid_of)"; then
    kill "$pid" 2>/dev/null || true
    local i=0
    while [ "$i" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.25
      i=$((i + 1))
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    ok "已停止（PID $pid）"
  else
    warn "没有在运行的进程"
  fi
  rm -f "$PWDFILE"
  return 0
}

do_status() {
  if have_systemd && [ -f "$SERVICE" ]; then
    if systemctl is-active --quiet "$NAME" 2>/dev/null; then
      ok "运行中（systemd 服务：$NAME）"
    else
      warn "未运行（systemd 服务：$NAME）"
    fi
    banner
    return 0
  fi
  local pid
  if pid="$(pid_of)"; then
    ok "运行中（PID $pid）"
  else
    warn "未运行"
  fi
  banner
}

case "${1:-start}" in
  start|"")        do_start ;;
  restart)         do_stop; sleep 1; do_start ;;
  stop)            do_stop ;;
  status)          do_status ;;
  logs)            tail -n 60 -f "$LOG" 2>/dev/null || { err "暂无日志：$LOG"; exit 1; } ;;
  *)               echo "用法：./已部署后启动.sh [start|restart|stop|status|logs]" ;;
esac
