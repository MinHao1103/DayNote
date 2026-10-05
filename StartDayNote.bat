@echo off
chcp 65001 > NUL
rem 啟動 DayNote：點兩下執行。若 DayNote 已開啟，舊的會自動關閉，只留新開的。
cd /d "%~dp0"
set "APP=%~dp0app\daynote.pyw"
rem 1. 可攜版內附的 Python（runtime 資料夾）
if exist "%~dp0runtime\pythonw.exe" (
  start "" "%~dp0runtime\pythonw.exe" "%APP%"
  exit /b 0
)
rem 2. PATH 裡的 pythonw
where pythonw > NUL 2> NUL
if not errorlevel 1 (
  start "" pythonw "%APP%"
  exit /b 0
)
rem 3. 安裝 Python 時沒勾「Add to PATH」：改用 Python 啟動器 pyw
where pyw > NUL 2> NUL
if not errorlevel 1 (
  start "" pyw -3 "%APP%"
  exit /b 0
)
echo 找不到 Python。請下載 DayNote 可攜版，或安裝 Python 3.10 以上（https://www.python.org/downloads/）。
pause
exit /b 1
