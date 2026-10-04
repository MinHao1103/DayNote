#!/usr/bin/env bash
# DayNote 啟動腳本（Git Bash）
# 可重複執行：若 DayNote 已在執行，先關閉舊的再開新的。
set -u
cd "$(dirname "$0")" || exit 1
PID_FILE="daynote.pid"

# 確認 PID 存在且是 Python 程式（避免 PID 被其他程式重複使用時誤關）
is_daynote() {
  tasklist //FI "PID eq $1" //FO CSV //NH 2>/dev/null | grep -qi '^"python'
}

if [ -f "$PID_FILE" ]; then
  old_pid=$(tr -dc '0-9' < "$PID_FILE")
  if [ -n "$old_pid" ] && is_daynote "$old_pid"; then
    echo "DayNote 已在執行（PID $old_pid），關閉中…"
    rm -f "$PID_FILE"   # DayNote 每秒檢查 PID 檔，被移除就會自行正常關閉並存好視窗位置
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      is_daynote "$old_pid" || break
      sleep 0.5
    done
    if is_daynote "$old_pid"; then
      echo "5 秒內未關閉，強制結束"
      taskkill //F //PID "$old_pid" > /dev/null 2>&1
    fi
  fi
  rm -f "$PID_FILE"
fi

PYTHONW=$(command -v pythonw || true)
if [ -z "$PYTHONW" ]; then
  echo "找不到 pythonw，請確認已安裝 Python 3.10 以上並加入 PATH"
  exit 1
fi

# 透過 cmd start 啟動，與終端機脫離：關閉 Git Bash 視窗不會連帶關閉 DayNote
cmd //c start "" "$(cygpath -w "$PYTHONW")" "$(cygpath -w "$PWD/daynote.pyw")"
echo "DayNote 已啟動"
