#!/usr/bin/env bash
# YueWuBot 部署脚本 —— Linux / macOS 通用
# 用法: ./start.sh [start|stop|restart|status|logs|token|fg|deps|doctor|uninstall|help]
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
[ -n "${APP_DIR:-}" ] || { echo "无法定位脚本目录"; exit 1; }
cd "$APP_DIR" || exit 1

NAME="yuewubot"
PORT="${YW_PORT:-6199}"
HOST="${YW_HOST:-127.0.0.1}"
VENV="$APP_DIR/.venv"
PY_BIN="$VENV/bin/python"
PIP_MIRROR_1="${YW_MIRROR:-https://pypi.mirrors.ustc.edu.cn/simple}"
PIP_MIRROR_2="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
SERVICE="/etc/systemd/system/${NAME}.service"
LOGDIR="$APP_DIR/logs"
LOG="$LOGDIR/app.log"
PWDFILE="$APP_DIR/.pid"
ACCEPTED="$APP_DIR/data/.disclaimer_accepted"
PIPLOG="$APP_DIR/.pip-install.log"

if [ -t 1 ]; then
  C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_END="\033[0m"
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_END=""
fi
ok()   { printf "${C_OK}[OK]${C_END} %s\n" "$*"; }
warn() { printf "${C_WARN}[!]${C_END} %s\n" "$*"; }
err()  { printf "${C_ERR}[FAIL]${C_END} %s\n" "$*" >&2; }
dim()  { printf "${C_DIM}%s${C_END}\n" "$*"; }
hr()   { printf '%s\n' "---------------------------------------------------------"; }

OS_KIND="other"
case "$(uname -s 2>/dev/null)" in
  Linux*)  OS_KIND="linux" ;;
  Darwin*) OS_KIND="macos" ;;
esac

lower() { printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'; }

is_yes() {
  case "$(lower "${1:-}")" in
    y|yes|1|true) return 0 ;;
  esac
  return 1
}

pkg_manager() {
  for m in apt-get dnf yum apk pacman zypper brew; do
    command -v "$m" >/dev/null 2>&1 && { echo "$m"; return 0; }
  done
  return 1
}

python_hint() {
  warn "未找到可用的 Python 3.6+，请先安装（推荐 3.8 及以上）："
  case "$(pkg_manager || true)" in
    apt-get) dim "  sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip" ;;
    dnf)     dim "  sudo dnf install -y python3 python3-pip" ;;
    yum)     dim "  sudo yum install -y python3 python3-pip" ;;
    apk)     dim "  sudo apk add python3 py3-pip" ;;
    pacman)  dim "  sudo pacman -S --needed python" ;;
    zypper)  dim "  sudo zypper install -y python3 python3-pip" ;;
    brew)    dim "  brew install python" ;;
    *)       dim "  请到 https://www.python.org/downloads/ 下载安装" ;;
  esac
}

venv_hint() {
  warn "创建虚拟环境失败，通常是缺少 venv 模块："
  case "$(pkg_manager || true)" in
    apt-get) dim "  sudo apt-get install -y python3-venv" ;;
    dnf|yum) dim "  sudo yum install -y python3-virtualenv 或 python3 -m pip install virtualenv" ;;
    apk)     dim "  sudo apk add python3-dev" ;;
    *)       dim "  执行: python3 -m pip install --user virtualenv" ;;
  esac
}

collect_pythons() {
  PY_LIST=""
  local c v path
  for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3.6 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null || echo 0)"
    case "$v" in ''|*[!0-9]*) continue ;; esac
    [ "$v" -ge 306 ] || continue
    path="$(command -v "$c")"
    case " $PY_LIST " in *" $path "*) continue ;; esac
    PY_LIST="$PY_LIST $path"
  done
  [ -n "$PY_LIST" ]
}

python_has_venv() {
  "$1" -c "import venv, ensurepip" >/dev/null 2>&1
}

need_python() {
  collect_pythons || { python_hint; exit 1; }
  PY=""
  local c
  # 优先选择自带 venv + ensurepip 的解释器，避免选中残缺的系统 Python
  for c in $PY_LIST; do
    if python_has_venv "$c"; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    PY="$(printf '%s\n' $PY_LIST | head -n 1)"
    warn "未找到自带 venv 模块的 Python，将尝试用 virtualenv 兜底"
  fi
  PY_VER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "?")"
  dim "Python：$PY  ($PY_VER)"
  case "$PY_VER" in
    3.6.*) warn "Python 3.6 已停止维护，建议升级到 3.8 及以上" ;;
  esac
}

detect_arch() {
  case "$(uname -m 2>/dev/null | tr '[:upper:]' '[:lower:]')" in
    x86_64|amd64)        echo "x86_64" ;;
    aarch64|arm64)       echo "arm64" ;;
    i386|i486|i586|i686) echo "x86" ;;
    *)                   echo "$(uname -m 2>/dev/null || echo unknown)" ;;
  esac
}

show_disclaimer() {
  cat <<'EOF'

==========================================================================
                        YueWuBot 免责声明
==========================================================================

本软件按"现状"提供，仅供学习与技术交流使用。继续使用即表示你已完整阅读、
理解并同意以下全部条款：

1. 本软件通过 OneBot v11 协议与第三方即时通讯平台对接，属于非官方客户端
   工具。使用第三方客户端或自动化机器人可能违反对应平台的服务条款，存在
   账号被限制、冻结或封禁的风险，该风险由使用者自行承担。

2. 使用者应确保自身行为符合所在地区法律法规及平台规则，不得将本软件用于
   发送垃圾信息、骚扰、诈骗、侵犯他人隐私或任何违法违规用途。

3. 本软件及作者不对因使用、误用或无法使用本软件所导致的任何直接或间接
   损失承担责任，包括但不限于账号、数据丢失与业务中断。

4. 本软件为开源软件，使用者可自行审阅全部源码；插件与模型接口由使用者
   自行配置，其产生的内容与后果由使用者负责。

==========================================================================
EOF
}

require_disclaimer() {
  [ -f "$ACCEPTED" ] && return 0
  if is_yes "${YW_ACCEPT:-}"; then
    if ! mkdir -p "$APP_DIR/data" || ! printf 'accepted\n' > "$ACCEPTED"; then
      err "无法写入 $ACCEPTED，请检查目录权限"
      exit 1
    fi
    ok "已同意免责声明（YW_ACCEPT）"
    return 0
  fi
  show_disclaimer
  if [ ! -t 0 ]; then
    err "当前为非交互环境，请先执行：YW_ACCEPT=Y ./start.sh"
    exit 1
  fi
  printf "\n输入 Y 或 yes 表示同意并继续（其他输入取消）："
  if ! IFS= read -r _ans; then
    err "未读取到确认，已取消"
    exit 1
  fi
  if is_yes "$_ans"; then
    mkdir -p "$APP_DIR/data" && printf 'accepted\n' > "$ACCEPTED"
    ok "已同意免责声明"
  else
    err "未同意免责声明，安装已取消"
    exit 1
  fi
}

check_writable() {
  if ! ( : > "$APP_DIR/.write-test" ) 2>/dev/null; then
    err "目录不可写：$APP_DIR"
    dim "  请把程序放到有写权限的目录，或用 sudo 运行"
    exit 1
  fi
  rm -f "$APP_DIR/.write-test"
}

ensure_venv() {
  if [ -x "$PY_BIN" ] && "$PY_BIN" -c "import sys" >/dev/null 2>&1; then
    return 0
  fi
  if [ -d "$VENV" ]; then
    warn "检测到损坏的虚拟环境，正在重建"
    rm -rf "$VENV"
  fi
  ok "创建虚拟环境 .venv"
  local c
  for c in ${PY_LIST:-}; do
    rm -rf "$VENV"
    if "$c" -m venv "$VENV" >/dev/null 2>&1 && [ -x "$PY_BIN" ]; then
      PY="$c"
      PY_VER="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "?")"
      ok "虚拟环境已创建（$PY $PY_VER）"
      return 0
    fi
    rm -rf "$VENV"
    if "$c" -m virtualenv "$VENV" >/dev/null 2>&1 && [ -x "$PY_BIN" ]; then
      PY="$c"
      PY_VER="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "?")"
      ok "虚拟环境已创建（virtualenv，$PY $PY_VER）"
      return 0
    fi
  done
  rm -rf "$VENV"
  venv_hint
  exit 1
}

ensure_pip() {
  if "$PY_BIN" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  warn "虚拟环境内 pip 不可用，尝试用 ensurepip 修复"
  "$PY_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
  if "$PY_BIN" -m pip --version >/dev/null 2>&1; then
    ok "pip 已修复"
    return 0
  fi
  err "pip 修复失败，请执行：$PY_BIN -m ensurepip --upgrade"
  return 1
}

upgrade_pip() {
  local m
  for m in "$PIP_MIRROR_1" "$PIP_MIRROR_2" ""; do
    if [ -n "$m" ]; then
      "$PY_BIN" -m pip install -q -U pip -i "$m" --timeout 30 --retries 1 \
        --disable-pip-version-check >>"$PIPLOG" 2>&1 && return 0
    else
      "$PY_BIN" -m pip install -q -U pip --timeout 30 --retries 1 \
        --disable-pip-version-check >>"$PIPLOG" 2>&1 && return 0
    fi
  done
  warn "pip 升级失败，继续使用当前版本"
  return 0
}

deps_ok() {
  "$PY_BIN" -c "import aiohttp,sys
v=aiohttp.__version__.split('.')
sys.exit(0 if (int(v[0]),int(v[1]))>=(3,4) else 1)" >/dev/null 2>&1
}

install_deps() {
  local m
  : > "$PIPLOG"
  for m in "$PIP_MIRROR_1" "$PIP_MIRROR_2" ""; do
    if [ -n "$m" ]; then
      dim "尝试依赖源：$m"
      if "$PY_BIN" -m pip install -q "aiohttp>=3.4,<4" -i "$m" \
          --timeout 60 --retries 2 --disable-pip-version-check >>"$PIPLOG" 2>&1; then
        ok "依赖安装完成（$m）"
        return 0
      fi
    else
      dim "尝试依赖源：PyPI 默认"
      if "$PY_BIN" -m pip install -q "aiohttp>=3.4,<4" \
          --timeout 60 --retries 2 --disable-pip-version-check >>"$PIPLOG" 2>&1; then
        ok "依赖安装完成（PyPI 默认源）"
        return 0
      fi
    fi
    warn "该源安装失败，切换下一个"
  done
  return 1
}

ensure_deps() {
  if deps_ok; then
    ok "依赖已就绪（aiohttp）"
    return 0
  fi
  ok "安装依赖 aiohttp"
  dim "平台：$OS_KIND / $(detect_arch)"
  ensure_pip || exit 1
  upgrade_pip
  if ! install_deps; then
    err "依赖安装失败，最后 20 行日志："
    hr
    tail -n 20 "$PIPLOG" 2>/dev/null
    hr
    dim "完整日志：$PIPLOG"
    dim "可尝试换源：YW_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple ./start.sh deps"
    if ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then
      warn "系统未安装 C 编译器，若需从源码编译请先安装 gcc / build-essential"
    fi
    exit 1
  fi
}

have_systemd() {
  [ "$OS_KIND" = "linux" ] || return 1
  [ "$(id -u 2>/dev/null)" = "0" ] || return 1
  command -v systemctl >/dev/null 2>&1 || return 1
  [ -d /run/systemd/system ] || return 1
  [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ] || return 1
}

write_service() {
  if [ -f "$SERVICE" ]; then
    cp -f "$SERVICE" "$SERVICE.bak" 2>/dev/null || true
    dim "已备份原服务文件：$SERVICE.bak"
  fi
  cat > "$SERVICE" <<EOF
[Unit]
Description=YueWuBot
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=YW_HOST=$HOST
Environment=YW_PORT=$PORT
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY_BIN $APP_DIR/yuewubot.py
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload >/dev/null 2>&1 || true
}

port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -qE "[:.]${PORT}$" && return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -E "[.:]${PORT}[[:space:]].*LISTEN" >/dev/null 2>&1 && return 0
  fi
  return 1
}

pid_of() {
  [ -f "$PWDFILE" ] || return 1
  local p
  p="$(cat "$PWDFILE" 2>/dev/null | tr -dc '0-9')"
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  case "$(ps -p "$p" -o command= 2>/dev/null)" in
    *yuewubot.py*) echo "$p"; return 0 ;;
  esac
  return 1
}

wait_ready() {
  "$PY_BIN" - "$PORT" <<'PY' 2>/dev/null
import socket, sys, time
port = int(sys.argv[1])
for _ in range(40):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        sys.exit(0)
    except Exception:
        time.sleep(0.25)
    finally:
        try: s.close()
        except Exception: pass
sys.exit(1)
PY
}

token_ready() {
  [ -f "$APP_DIR/data/config.json" ] || return 1
  "$PY_BIN" - "$APP_DIR/data/config.json" <<'PY' 2>/dev/null
import json, re, sys
try:
    t = (json.load(open(sys.argv[1])).get("auth") or {}).get("token") or ""
    sys.exit(0 if (re.match(r"^[A-Za-z0-9]{30}$", t) and re.search(r"[A-Za-z]", t) and re.search(r"[0-9]", t)) else 1)
except Exception:
    sys.exit(1)
PY
}

ensure_token() {
  [ -n "${YW_SKIP_TOKEN:-}" ] && return 0
  [ -n "${YW_SKIP_ACCOUNT:-}" ] && return 0
  token_ready && return 0
  echo
  hr
  printf " 第 1 步：生成控制台访问令牌\n"
  dim "  令牌为 30 位字母与数字，登录控制台时粘贴使用"
  dim "  生成后会打印在终端，同时写入 data/TOKEN.txt"
  hr
  if [ -x "$PY_BIN" ]; then
    if ! "$PY_BIN" "$APP_DIR/yuewubot.py" --set-token; then
      warn "令牌生成失败，可稍后执行：./start.sh token --reset"
    fi
  else
    warn "Python 环境尚未就绪，稍后可执行：./start.sh token --reset"
  fi
  echo
}

show_token() {
  local token=""
  if [ -f "$APP_DIR/data/config.json" ] && [ -x "$PY_BIN" ]; then
    token="$("$PY_BIN" - "$APP_DIR/data/config.json" <<'PY' 2>/dev/null
import json, sys
try:
    print((json.load(open(sys.argv[1])).get("auth") or {}).get("token") or "")
except Exception:
    print("")
PY
)"
  fi
  local ui=""
  [ -f "$APP_DIR/astrbot_compat.py" ] && ui="/astrbot/"
  echo
  dim "控制台：http://127.0.0.1:$PORT$ui"
  if [ "$HOST" = "0.0.0.0" ]; then
    dim "局域网：http://$(lan_ip):$PORT$ui"
  fi
  if [ -n "$token" ]; then
    dim "令牌：  $token"
    dim "文件：  data/TOKEN.txt"
  else
    warn "尚未生成访问令牌，执行：./start.sh token --reset"
  fi
  dim "查看令牌：./start.sh token      重置：./start.sh token --reset"
  echo
}

lan_ip() {
  if [ "$OS_KIND" = "macos" ]; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "本机IP"
  else
    hostname -I 2>/dev/null | awk '{print $1}' || echo "本机IP"
  fi
}

rotate_log() {
  mkdir -p "$LOGDIR" 2>/dev/null || true
  if [ -f "$LOG" ]; then
    local sz
    sz="$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')"
    case "$sz" in ''|*[!0-9]*) sz=0 ;; esac
    if [ "$sz" -gt 5242880 ]; then
      mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    fi
  fi
}

start_systemd() {
  ensure_venv
  ensure_deps
  ensure_token
  write_service
  systemctl enable "$NAME" >/dev/null 2>&1 || true
  systemctl restart "$NAME" >/dev/null 2>&1 || true
  sleep 2
  if systemctl is-active --quiet "$NAME" 2>/dev/null; then
    ok "已启动（systemd 服务：$NAME）"
  else
    err "服务启动失败，最近日志："
    hr
    journalctl -u "$NAME" -n 20 --no-pager 2>/dev/null || true
    hr
    dim "查看完整日志：journalctl -u $NAME -n 100 --no-pager"
    exit 1
  fi
  show_token
}

start_plain() {
  ensure_venv
  ensure_deps
  ensure_token
  local pid
  if pid="$(pid_of)"; then
    warn "已在运行（PID $pid），如需重启：./start.sh restart"
    return 0
  fi
  if port_busy; then
    err "端口 $PORT 已被占用"
    dim "  换端口启动：YW_PORT=6200 ./start.sh"
    dim "  查看占用：lsof -nP -iTCP:$PORT -sTCP:LISTEN 或 ss -lntp | grep $PORT"
    exit 1
  fi
  rotate_log
  nohup env YW_HOST="$HOST" YW_PORT="$PORT" PYTHONUNBUFFERED=1 \
    "$PY_BIN" "$APP_DIR/yuewubot.py" >>"$LOG" 2>&1 &
  pid=$!
  echo "$pid" > "$PWDFILE"
  if wait_ready; then
    ok "已启动（PID $pid）"
  else
    if kill -0 "$pid" 2>/dev/null; then
      warn "进程已启动但端口未就绪，请查看日志"
    else
      err "启动失败，最近日志："
      hr
      tail -n 20 "$LOG" 2>/dev/null
      hr
      rm -f "$PWDFILE"
      exit 1
    fi
  fi
  show_token
}

cmd_start() {
  require_disclaimer
  check_writable
  need_python
  if have_systemd; then
    start_systemd
  else
    start_plain
    if [ "$OS_KIND" = "macos" ]; then
      dim "提示：首次运行如被系统拦截，可执行 xattr -dr com.apple.quarantine \"$APP_DIR\""
    fi
  fi
}

cmd_stop() {
  if have_systemd && [ -f "$SERVICE" ]; then
    systemctl stop "$NAME" >/dev/null 2>&1 || true
    if systemctl is-active --quiet "$NAME" 2>/dev/null; then
      err "停止失败，请执行：systemctl stop $NAME"
      return 1
    fi
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
    if kill -0 "$pid" 2>/dev/null; then
      warn "进程未退出，强制结束"
      kill -9 "$pid" 2>/dev/null || true
    fi
    ok "已停止（PID $pid）"
  else
    warn "没有在运行的进程"
  fi
  rm -f "$PWDFILE"
  return 0
}

cmd_status() {
  local running=0
  if have_systemd && [ -f "$SERVICE" ]; then
    if systemctl is-active --quiet "$NAME" 2>/dev/null; then
      ok "systemd 服务运行中：$NAME"
      running=1
    else
      warn "systemd 服务未运行"
    fi
  fi
  local pid
  if pid="$(pid_of)"; then
    ok "进程运行中（PID $pid，RSS $(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')）"
    running=1
  fi
  [ "$running" = "1" ] || warn "YueWuBot 未运行"
  if port_busy; then
    dim "端口 $PORT 正在监听"
  else
    dim "端口 $PORT 未监听"
  fi
  if [ -f "$APP_DIR/data/config.json" ]; then
    dim "配置文件：$APP_DIR/data/config.json"
  fi
  return 0
}

cmd_doctor() {
  hr
  ok "环境诊断"
  dim "系统：$OS_KIND / $(uname -s 2>/dev/null) $(uname -r 2>/dev/null) / $(detect_arch)"
  dim "目录：$APP_DIR"
  if collect_pythons; then
    for p in $PY_LIST; do
      dim "Python：$p ($("$p" -V 2>&1))"
    done
  else
    err "Python 3.6+ 未找到"
  fi
  if [ -x "$PY_BIN" ]; then
    dim "虚拟环境：已创建"
    "$PY_BIN" -m pip --version 2>/dev/null | sed 's/^/  pip: /'
    if deps_ok; then dim "依赖：aiohttp 已就绪"; else warn "依赖：aiohttp 未安装"; fi
  else
    warn "虚拟环境：未创建"
  fi
  if have_systemd; then dim "服务管理：systemd"; else dim "服务管理：后台进程（nohup）"; fi
  if port_busy; then warn "端口 $PORT：已占用"; else dim "端口 $PORT：可用"; fi
  if [ -f "$APP_DIR/data/config.json" ]; then dim "配置：已生成"; else dim "配置：未生成"; fi
  hr
}

cmd_logs() {
  if have_systemd && [ -f "$SERVICE" ]; then
    journalctl -u "$NAME" -f -n 60 --no-pager
  else
    [ -f "$LOG" ] || { err "暂无日志：$LOG"; exit 1; }
    tail -n 60 -f "$LOG"
  fi
}

cmd_token() {
  local py="$PY_BIN"
  if [ ! -x "$py" ]; then
    if command -v python3 >/dev/null 2>&1; then
      py="$(command -v python3)"
    else
      err "找不到 Python，请先执行 ./start.sh 完成部署"
      return 1
    fi
  fi
  if [ ! -f "$APP_DIR/data/config.json" ]; then
    err "配置文件尚未生成，请先执行 ./start.sh"
    return 1
  fi
  case "${1:-}" in
    --reset|-r|reset) exec "$py" "$APP_DIR/yuewubot.py" --reset-token ;;
    --set|-s|set)
      if [ -n "${2:-}" ]; then
        exec "$py" "$APP_DIR/yuewubot.py" --set-token "$2"
      fi
      exec "$py" "$APP_DIR/yuewubot.py" --set-token ;;
    *) exec "$py" "$APP_DIR/yuewubot.py" --show-token ;;
  esac
}

cmd_setpass() {
  warn "1.4.2 起控制台改用访问令牌，不再使用账号密码"
  cmd_token "${1:-}"
}

cmd_account() {
  cmd_token "${1:-}"
}

cmd_fg() {
  require_disclaimer
  check_writable
  need_python
  ensure_venv
  ensure_deps
  mkdir -p "$LOGDIR"
  ok "前台运行，Ctrl+C 退出"
  YW_HOST="$HOST" YW_PORT="$PORT" exec "$PY_BIN" "$APP_DIR/yuewubot.py"
}

cmd_deps() {
  check_writable
  need_python
  ensure_venv
  ensure_deps
  cmd_doctor
}

cmd_uninstall() {
  cmd_stop
  if have_systemd && [ -f "$SERVICE" ]; then
    systemctl disable "$NAME" >/dev/null 2>&1 || true
    rm -f "$SERVICE"
    systemctl daemon-reload >/dev/null 2>&1 || true
    ok "已移除 systemd 服务"
  fi
  echo
  warn "以下内容保留，确认不需要可手动删除："
  dim "  $VENV        依赖环境"
  dim "  $APP_DIR/data      配置与账号"
  dim "  $APP_DIR/plugins   已装插件"
}

usage() {
  cat <<'EOF'
YueWuBot v1.4.2 部署脚本（Linux / macOS）

  ./start.sh            一键安装并启动（首次需同意免责声明）
  ./start.sh token      查看控制台访问令牌
  ./start.sh token --reset   重新生成令牌（旧令牌立即失效）
  ./start.sh fg         前台运行，Ctrl+C 退出（调试用）
  ./start.sh deps       仅安装/修复依赖
  ./start.sh doctor     环境诊断（Python、依赖、端口、权限）
  ./start.sh status     运行状态
  ./start.sh stop       停止
  ./start.sh restart    重启
  ./start.sh logs       实时日志
  ./start.sh uninstall  移除服务（保留数据）

部署完成后日常启动请用： ./已部署后启动.sh

依赖源：默认中科大，失败自动切换清华，最后回退 PyPI 官方源。

环境变量：
  YW_ACCEPT=Y                非交互同意免责声明
  YW_SKIP_TOKEN=1            安装时跳过令牌生成（之后用 token 命令生成）
  YW_PORT=6199               监听端口
  YW_HOST=127.0.0.1          监听地址，对外可设 0.0.0.0
  YW_MIRROR=...              主依赖源
  PIP_MIRROR=...             备用依赖源

Windows 用户请改用：powershell -ExecutionPolicy Bypass -File .\install.ps1
EOF
}

ACTION="${1:-start}"
case "$ACTION" in
  start)                 cmd_start ;;
  fg|foreground|run)     cmd_fg ;;
  deps|install)          cmd_deps ;;
  doctor|check)          cmd_doctor ;;
  stop)                  cmd_stop ;;
  restart)               cmd_stop; sleep 1; cmd_start ;;
  status)                cmd_status ;;
  logs)                  cmd_logs ;;
  account|token)         shift; cmd_token "${1:-}" ;;
  setpass|passwd|setpw)  shift; cmd_setpass "${1:-}" ;;
  uninstall|remove)      cmd_uninstall ;;
  help|-h|--help|*)      usage ;;
esac
