@echo off
setlocal

net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set "ROOT=%~dp0"
set "VENV_DIR=%~dp0.venv"
set "PYTHON=python"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  "%PYTHON%" -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

python -m pip install --upgrade pip
pushd "%ROOT%"
python -m pip install .
popd

set "RDPWRAP_BIN=%ROOT%utils\rdpwrap\bin"
set "AGENT_USER=LoOperAgent"
set "AGENT_PASS=LoOperPassword123!"

if not exist "%RDPWRAP_BIN%\RDPWInst.exe" (
  echo Downloading RDPWrap release package...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$zip=Join-Path $env:TEMP 'RDPWrap-v1.6.2.zip';" ^
    "Invoke-WebRequest -Uri 'https://github.com/stascorp/rdpwrap/releases/download/v1.6.2/RDPWrap-v1.6.2.zip' -OutFile $zip;" ^
    "$tmp=Join-Path $env:TEMP ('RDPWrapExtract_' + [guid]::NewGuid().ToString('N'));" ^
    "New-Item -ItemType Directory -Path $tmp | Out-Null;" ^
    "Expand-Archive -Path $zip -DestinationPath $tmp -Force;" ^
    "Get-ChildItem -Path $tmp -Recurse -Filter '*.exe' | ForEach-Object { Copy-Item -Path $_.FullName -Destination '%RDPWRAP_BIN%' -Force };" ^
    "Remove-Item -Path $zip -Force;" ^
    "Remove-Item -Path $tmp -Recurse -Force;"
)

if exist "%RDPWRAP_BIN%\RDPWInst.exe" (
  echo Installing RDPWrap...
  "%RDPWRAP_BIN%\RDPWInst.exe" -i -o
  "%RDPWRAP_BIN%\RDPWInst.exe" -w

  if exist "C:\Program Files\RDP Wrapper" (
    echo Fetching latest community rdpwrap.ini...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "try { Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/sebaxakerhtc/rdpwrap.ini/master/rdpwrap.ini' -OutFile 'C:\Program Files\RDP Wrapper\rdpwrap.ini' -UseBasicParsing } catch { }"
    net stop TermService /y >nul 2>&1
    net start TermService >nul 2>&1
  )
) else (
  echo RDPWInst.exe not found; skipping RDPWrap install.
)

net user "%AGENT_USER%" >nul 2>&1
if %errorlevel% neq 0 (
  echo Creating local agent user "%AGENT_USER%"...
  net user "%AGENT_USER%" "%AGENT_PASS%" /add /passwordchg:no /expires:never >nul
)

echo Adding "%AGENT_USER%" to Remote Desktop Users...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$u='%AGENT_USER%';" ^
  "$sid=New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-555');" ^
  "$g=$sid.Translate([System.Security.Principal.NTAccount]).Value.Split('\')[-1];" ^
  "cmd /c ('net localgroup ' + ('\"' + $g + '\"') + ' ' + $u + ' /add') | Out-Null"

echo Adding "%AGENT_USER%" to Administrators...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$u='%AGENT_USER%';" ^
  "$sid=New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544');" ^
  "$g=$sid.Translate([System.Security.Principal.NTAccount]).Value.Split('\')[-1];" ^
  "cmd /c ('net localgroup ' + ('\"' + $g + '\"') + ' ' + $u + ' /add') | Out-Null"

echo Enabling alternate shell support...
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList" /v fDisabledAllowList /t REG_DWORD /d 1 /f >nul 2>&1

echo Enabling Windows Firewall rules for Remote Desktop...
netsh advfirewall firewall set rule group="remote desktop" new enable=Yes >nul 2>&1

cmdkey /generic:TERMSRV/127.0.0.2 /user:%AGENT_USER% /pass:%AGENT_PASS% >nul 2>&1

python "%~dp0LoOper.py"
