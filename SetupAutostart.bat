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
rem 優先用可攜版內附的 Python，其次 PATH 裡的 pythonw，最後用 Python 啟動器 pyw
set "TARGET="
set "ARGS="
set "WORKDIR=%~dp0"
set "WORKDIR=%WORKDIR:~0,-1%"
set "APPFILE=%WORKDIR%\app\daynote.pyw"
if exist "%WORKDIR%\runtime\pythonw.exe" set "TARGET=%WORKDIR%\runtime\pythonw.exe"
for /f "delims=" %%i in ('where pythonw 2^>NUL') do if not defined TARGET set "TARGET=%%i"
if defined TARGET set ARGS="%APPFILE%"
if not defined TARGET for /f "delims=" %%i in ('where pyw 2^>NUL') do if not defined TARGET set "TARGET=%%i"
if not defined ARGS set ARGS=-3 "%APPFILE%"
if not defined TARGET (
  echo 找不到 Python，請安裝 Python 3.10 以上。
  exit /b 1
)
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
