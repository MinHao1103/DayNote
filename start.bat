@echo off
chcp 65001 > NUL
rem DayNote 啟動檔：點兩下執行。若 DayNote 已開啟，舊的會自動關閉，只留新開的。
cd /d "%~dp0"
rem 優先用 PATH 裡的 pythonw；安裝 Python 時沒勾「Add to PATH」則改用 Python 啟動器 pyw
where pythonw > NUL 2> NUL
if not errorlevel 1 (
  start "" pythonw "%~dp0daynote.pyw"
  exit /b 0
)
where pyw > NUL 2> NUL
if not errorlevel 1 (
  start "" pyw -3 "%~dp0daynote.pyw"
  exit /b 0
)
echo 找不到 Python，請安裝 Python 3.10 以上（https://www.python.org/downloads/）。
pause
exit /b 1
