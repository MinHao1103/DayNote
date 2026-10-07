@echo off
chcp 65001 > NUL
rem 啟動 DayNote：點兩下執行。若 DayNote 已開啟，舊的會自動關閉，只留新開的。
rem 順序：電腦上安裝的 Python（PATH 裡的 pythonw → Python 啟動器 pyw）優先，最後才用可攜版內附的 runtime。
rem 公司電腦常會擋「從下載的 zip 解壓縮出來的程式」，安裝好的 Python 則可以執行，所以以安裝的為主。
rem 每個候選都先確認有 tkinter、而且是 Python 3.10 以上才使用（pythonw 出錯不會顯示任何訊息）。
rem 與 StartDayNoteSilent.vbs、SetupAutostart.bat 的順序相同。
cd /d "%~dp0"
set "APP=%~dp0app\daynote.pyw"
set "PY="
set "PYARGS="
for /f "delims=" %%i in ('where pythonw 2^>NUL') do if not defined PY call :check "%%i"
if not defined PY for /f "delims=" %%i in ('where pyw 2^>NUL') do if not defined PY call :check "%%i" -3
if not defined PY if exist "%~dp0runtime\pythonw.exe" set "PY=%~dp0runtime\pythonw.exe"
if defined PY (
  start "" "%PY%" %PYARGS% "%APP%"
  exit /b 0
)
echo 找不到可以執行 DayNote 的 Python（需要 Python 3.10 以上，且安裝時勾選 tcl/tk）。
echo 請安裝 Python（https://www.python.org/downloads/），或下載 DayNote 可攜版。
pause
exit /b 1

rem 檢查候選的 Python：%~1＝路徑，%~2＝額外參數（pyw 用 -3）。可以執行 DayNote 就設定 PY、PYARGS。
rem WindowsApps 裡的 pythonw 只是商店的捷徑，沒有真的裝 Python 時執行會打開 Microsoft Store，所以先確認。
:check
set "CAND=%~1"
set "REST=%CAND:WindowsApps=%"
if not "%REST%"=="%CAND%" (
  dir /b /ad "%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.*" > NUL 2> NUL || exit /b 0
)
"%CAND%" %~2 -c "import sys,tkinter;sys.exit(0 if sys.version_info>=(3,10) else 1)" > NUL 2> NUL
if errorlevel 1 exit /b 0
set "PY=%CAND%"
set "PYARGS=%~2"
exit /b 0
