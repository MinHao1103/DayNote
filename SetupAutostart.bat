@echo off
chcp 65001 > NUL
rem DayNote 開機自動啟動設定：點兩下執行，或 SetupAutostart.bat on / off / status
rem 只用 Windows 內建的 cmd 與 PowerShell，不需要 Git Bash
cd /d "%~dp0"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DayNote.lnk"
set "ACTION=%~1"
if "%ACTION%"=="" goto menu
if /i "%ACTION%"=="on" goto on
if /i "%ACTION%"=="off" goto off
if /i "%ACTION%"=="status" goto status
echo 用法：SetupAutostart.bat [on^|off^|status]
exit /b 1

:menu
call :status
echo.
choice /c 120 /n /m "[1] 開啟  [2] 關閉  [0] 離開："
if errorlevel 3 exit /b 0
if errorlevel 2 (call :off & pause & exit /b 0)
call :on
pause
exit /b 0

:on
rem 順序與 StartDayNote.bat 相同：電腦上安裝的 Python 優先，最後才用可攜版內附的 runtime
rem 捷徑直接指向 pythonw（不經過 vbs／bat），公司電腦比較不會擋
set "WORKDIR=%~dp0"
set "WORKDIR=%WORKDIR:~0,-1%"
set "APPFILE=%WORKDIR%\app\daynote.pyw"
set "PY="
set "PYARGS="
for /f "delims=" %%i in ('where pythonw 2^>NUL') do if not defined PY call :check "%%i"
if not defined PY for /f "delims=" %%i in ('where pyw 2^>NUL') do if not defined PY call :check "%%i" -3
if not defined PY if exist "%WORKDIR%\runtime\pythonw.exe" set "PY=%WORKDIR%\runtime\pythonw.exe"
if not defined PY (
  echo 找不到可以執行 DayNote 的 Python，請安裝 Python 3.10 以上（安裝時勾選 tcl/tk）。
  exit /b 1
)
set "TARGET=%PY%"
set ARGS=%PYARGS% "%APPFILE%"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); $s.TargetPath=$env:TARGET; $s.Arguments=$env:ARGS; $s.WorkingDirectory=$env:WORKDIR; $s.Description='DayNote'; $s.Save()"
echo 已開啟開機自動啟動。
exit /b 0

:off
if exist "%LINK%" del "%LINK%"
echo 已關閉開機自動啟動。
exit /b 0

:status
if exist "%LINK%" (echo 開機自動啟動：開啟) else (echo 開機自動啟動：關閉)
exit /b 0

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
