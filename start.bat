@echo off
chcp 65001 > NUL
rem DayNote 啟動檔：點兩下執行。若 DayNote 已開啟，舊的會自動關閉，只留新開的。
cd /d "%~dp0"
where pythonw > NUL 2> NUL
if errorlevel 1 (
  echo 找不到 pythonw，請確認已安裝 Python 3.10 以上並加入 PATH。
  pause
  exit /b 1
)
start "" pythonw "%~dp0daynote.pyw"
