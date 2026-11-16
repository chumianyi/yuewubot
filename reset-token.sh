#!/usr/bin/env bash
# YueWuBot 重置控制台访问令牌
# 用法：./reset-token.sh            随机生成新的 30 位令牌
#       ./reset-token.sh 自定义令牌   使用指定的 30 位字母数字令牌
# 旧令牌立即失效；新令牌同时写入 data/TOKEN.txt
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

PY=""
for c in "$APP_DIR/.venv/bin/python" "$APP_DIR/venv/bin/python"; do
  if [ -x "$c" ]; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi

if [ -z "$PY" ]; then
  echo "未找到 Python，请先执行 ./start.sh 完成安装。" >&2
  exit 1
fi

if [ ! -f "$APP_DIR/yuewubot.py" ]; then
  echo "未找到 yuewubot.py，请在安装目录内执行本脚本。" >&2
  exit 1
fi

if [ "$#" -ge 1 ] && [ -n "${1:-}" ]; then
  exec "$PY" "$APP_DIR/yuewubot.py" --set-token "$1"
fi

exec "$PY" "$APP_DIR/yuewubot.py" --reset-token
