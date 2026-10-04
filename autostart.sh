#!/usr/bin/env bash
# DayNote 開機自動啟動設定（Git Bash）
#   ./autostart.sh on      在 Windows「啟動」資料夾建立捷徑
#   ./autostart.sh off     移除捷徑
#   ./autostart.sh status  查看目前狀態
set -u
cd "$(dirname "$0")" || exit 1

STARTUP=$(powershell -NoProfile -Command "[Environment]::GetFolderPath('Startup')" | tr -d '\r')
LINK="$STARTUP\\DayNote.lnk"

case "${1:-status}" in
  on)
    PYTHONW=$(command -v pythonw || true)
    if [ -z "$PYTHONW" ]; then
      echo "找不到 pythonw，請確認已安裝 Python 3.10 以上並加入 PATH"
      exit 1
    fi
    TARGET=$(cygpath -w "$PYTHONW")
    APP_DIR=$(cygpath -w "$PWD")
    powershell -NoProfile -Command "
      \$s = (New-Object -ComObject WScript.Shell).CreateShortcut('$LINK');
      \$s.TargetPath = '$TARGET';
      \$s.Arguments = '\"$APP_DIR\\daynote.pyw\"';
      \$s.WorkingDirectory = '$APP_DIR';
      \$s.Description = 'DayNote';
      \$s.Save()"
    echo "已開啟開機自動啟動：$LINK"
    ;;
  off)
    powershell -NoProfile -Command "Remove-Item -LiteralPath '$LINK' -ErrorAction SilentlyContinue"
    echo "已關閉開機自動啟動"
    ;;
  status)
    if powershell -NoProfile -Command "exit !(Test-Path -LiteralPath '$LINK')"; then
      echo "開機自動啟動：開啟（$LINK）"
    else
      echo "開機自動啟動：關閉"
    fi
    ;;
  *)
    echo "用法：./autostart.sh [on|off|status]"
    exit 1
    ;;
esac
