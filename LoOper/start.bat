@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: ——————————————————————————————————————————————
:: 0. Jump into the script's own folder (avoids landing in System32)
:: ——————————————————————————————————————————————
pushd "%~dp0"

:: ——————————————————————————————————————————————
:: ASCII Art Header
:: ——————————————————————————————————————————————
cls
color 0A
echo.
echo     ██╗      ██████╗  ██████╗ █████╗ ██╗      ██████╗ ██████╗ ███████╗██████╗  █████╗ ████████╗ ██████╗ ██████╗ 
echo     ██║     ██╔═══██╗██╔════╝██╔══██╗██║     ██╔═══██╗██╔══██╗██╔════╝██╔══██╗██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗
echo     ██║     ██║   ██║██║     ███████║██║     ██║   ██║██████╔╝█████╗  ██████╔╝███████║   ██║   ██║   ██║██████╔╝
echo     ██║     ██║   ██║██║     ██╔══██║██║     ██║   ██║██╔═══╝ ██╔══╝  ██╔══██╗██╔══██║   ██║   ██║   ██║██╔══██╗
echo     ███████╗╚██████╔╝╚██████╗██║  ██║███████╗╚██████╔╝██║     ███████╗██║  ██║██║  ██║   ██║   ╚██████╔╝██║  ██║
echo     ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝
echo.
echo                                    🤖 AUTOMATED INSTALLATION SYSTEM 🤖
echo                                      Created by RODRIGO DAVID BENITEZ
echo.
echo     ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
echo.

:: ——————————————————————————————————————————————
:: 1. Ensure script is running as admin (needed for installations)
:: ——————————————————————————————————————————————
echo [🔐] Checking administrator privileges...
net session >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo     ❌ ERROR: This script must be run as Administrator!
    echo     ➤ Right-click on start.bat and select "Run as administrator"
    echo.
    pause
    exit /b 1
)
echo [✅] Administrator privileges confirmed!
echo.

:: ——————————————————————————————————————————————
:: 2. Python 3.13 Installation with Loading Animation
:: ——————————————————————————————————————————————
echo [🐍] Checking for Python 3.13...
py -3.13 --version >nul 2>&1
if errorlevel 1 (
    color 0E
    echo [⚠️] Python 3.13 not found. Installing now...
    echo.
    
    :: Loading animation function
    call :loading_animation "Preparing Python installation"
    
    :: 2a. Prefer winget if available
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo [📦] Installing Python 3.13 with winget...
        call :loading_animation "Downloading Python 3.13"
        winget install --id Python.Python.3.13 -e --source winget --accept-package-agreements --silent
    ) else (
        :: 2b. Fallback: download official installer & silent-install
        echo [🌐] Downloading Python 3.13 installer...
        call :loading_animation "Downloading from python.org"
        powershell -Command "Invoke-WebRequest `
            -Uri 'https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe' `
            -OutFile '%TEMP%\python313.exe'"

        echo [⚙️] Running silent installer...
        call :loading_animation "Installing Python 3.13"
        start /wait "" "%TEMP%\python313.exe" ^
            /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
    )

    :: Re-verify
    echo [🔍] Verifying Python 3.13 installation...
    py -3.13 --version >nul 2>&1
    if errorlevel 1 (
        color 0C
        echo.
        echo     ❌ ERROR: Python 3.13 installation failed!
        echo     ➤ Please install Python 3.13 manually from python.org
        echo.
        pause
        exit /b 1
    )
    color 0A
    echo [✅] Python 3.13 installed successfully!
) else (
    echo [✅] Python 3.13 found and ready!
)
echo.

:: ——————————————————————————————————————————————
:: 3. Tesseract OCR Installation
:: ——————————————————————————————————————————————
echo [👁️] Checking for Tesseract OCR...
if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    color 0E
    echo [⚠️] Tesseract OCR not found. Installing now...
    echo.
    
    call :loading_animation "Preparing Tesseract installation"
    
    :: Check if winget is available
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo [📦] Installing Tesseract OCR with winget...
        call :loading_animation "Downloading Tesseract OCR"
        winget install --id UB-Mannheim.TesseractOCR -e --source winget --accept-package-agreements --silent
    ) else (
        echo [🌐] Downloading Tesseract OCR installer...
        call :loading_animation "Downloading from GitHub"
        powershell -Command "Invoke-WebRequest `
            -Uri 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe' `
            -OutFile '%TEMP%\tesseract-setup.exe'"
        
        echo [⚙️] Installing Tesseract OCR...
        call :loading_animation "Installing Tesseract OCR"
        start /wait "" "%TEMP%\tesseract-setup.exe" /S
    )
    
    :: Add Tesseract to PATH
    echo [🔧] Adding Tesseract to system PATH...
    setx PATH "%PATH%;C:\Program Files\Tesseract-OCR" /M >nul 2>&1
    
    :: Verify installation
    echo [🔍] Verifying Tesseract installation...
    if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
        color 0A
        echo [✅] Tesseract OCR installed successfully!
    ) else (
        color 0C
        echo.
        echo     ❌ ERROR: Tesseract OCR installation failed!
        echo     ➤ Please install Tesseract manually from GitHub
        echo.
        pause
        exit /b 1
    )
) else (
    echo [✅] Tesseract OCR found and ready!
)
echo.

:: ——————————————————————————————————————————————
:: 4. Create & activate virtual environment
:: ——————————————————————————————————————————————
set VENV_DIR=venv
set VENV_PY=%VENV_DIR%\Scripts\python.exe

echo [🐍] Setting up Python virtual environment...
if not exist "%VENV_DIR%\" (
    echo [📦] Creating virtual environment...
    call :loading_animation "Creating Python virtual environment"
    py -3.13 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        color 0C
        echo.
        echo     ❌ ERROR: Virtual environment creation failed!
        echo.
        pause
        exit /b 1
    )
    echo [✅] Virtual environment created successfully!
) else (
    echo [✅] Virtual environment already exists!
)

:: Activate the virtual environment
echo [🔄] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    color 0C
    echo.
    echo     ❌ ERROR: Virtual environment activation failed!
    echo.
    pause
    exit /b 1
)
echo [✅] Virtual environment activated!
echo.

:: ——————————————————————————————————————————————
:: 5. Install/update dependencies
:: ——————————————————————————————————————————————
echo [📦] Installing and updating dependencies...
echo [⬆️] Upgrading pip...
call :loading_animation "Upgrading pip to latest version"
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
if errorlevel 1 (
    color 0E
    echo [⚠️] WARNING: Pip upgrade failed, continuing anyway...
) else (
    echo [✅] Pip upgraded successfully!
)

if exist requirements.txt (
    echo [📋] Installing dependencies from requirements.txt...
    call :loading_animation "Installing Python packages"
    "%VENV_PY%" -m pip install -r requirements.txt >nul 2>&1
    if errorlevel 1 (
        color 0C
        echo.
        echo     ❌ ERROR: Dependency installation failed!
        echo     ➤ Check requirements.txt for invalid packages
        echo.
        pause
        exit /b 1
    )
    echo [✅] All dependencies installed successfully!
) else (
    color 0E
    echo [⚠️] Warning: requirements.txt not found. Skipping dependencies.
)
echo.

:: ——————————————————————————————————————————————
:: 6. Environment Variables Setup for API Configuration
:: ——————————————————————————————————————————————
color 0D
echo.
echo     ⚙️ API CONFIGURATION SETUP ⚙️
echo     ═══════════════════════════════
echo.
echo [🌐] Setting up environment variables for API communication...
echo.
echo     This setup configures the LocalOperator API for:
echo     • Local server mode (localhost)
echo     • Tailscale network mode (remote server)
echo.

:: Check if environment variables are already set
if defined TAILSCALE_IP if defined API_PORT (
    echo [✅] Environment variables already configured:
    echo     • TAILSCALE_IP: %TAILSCALE_IP%
    echo     • API_PORT: %API_PORT%
    echo.
    set /p "reconfigure=Do you want to reconfigure? (y/N): "
    if /i "!reconfigure!" neq "y" goto :skip_env_setup
)

echo [📝] Choose your API configuration mode:
echo     1. Local mode (localhost) - API server runs on this machine
echo     2. Tailscale mode - API server runs on a remote Tailscale machine
echo     3. Skip configuration (use existing or defaults)
echo.
set /p "api_mode=Enter your choice (1-3): "

if "!api_mode!"=="1" (
    echo [🏠] Configuring for local mode...
    setx TAILSCALE_IP "localhost" >nul 2>&1
    setx TAILSCALE_SERVER_IP "localhost" >nul 2>&1
    set "TAILSCALE_IP=localhost"
    set "TAILSCALE_SERVER_IP=localhost"
    echo [✅] Local mode configured!
) else if "!api_mode!"=="2" (
    echo [🌐] Configuring for Tailscale mode...
    echo.
    echo     Enter the Tailscale IP address of the machine running the API server.
    echo     You can find this in your Tailscale admin panel or by running 'tailscale ip' on the server.
    echo.
    set /p "tailscale_server_ip=Tailscale Server IP: "
    
    if "!tailscale_server_ip!"=="" (
        color 0E
        echo [⚠️] No IP address provided. Using localhost as fallback.
        set "tailscale_server_ip=localhost"
    )
    
    setx TAILSCALE_SERVER_IP "!tailscale_server_ip!" >nul 2>&1
    set "TAILSCALE_SERVER_IP=!tailscale_server_ip!"
    
    echo.
    echo     If this machine will also run the API server, enter its Tailscale IP.
    echo     Otherwise, press Enter to skip.
    echo.
    set /p "tailscale_local_ip=This machine's Tailscale IP (optional): "
    
    if not "!tailscale_local_ip!"=="" (
        setx TAILSCALE_IP "!tailscale_local_ip!" >nul 2>&1
        set "TAILSCALE_IP=!tailscale_local_ip!"
    )
    
    echo [✅] Tailscale mode configured!
    echo     • Server IP: !tailscale_server_ip!
    if not "!tailscale_local_ip!"=="" echo     • Local IP: !tailscale_local_ip!
) else (
    echo [⏭️] Skipping API configuration...
)

:: Configure API port
if not "!api_mode!"=="3" (
    echo.
    set /p "api_port=API Port (default 8000): "
    if "!api_port!"=="" set "api_port=8000"
    
    setx API_PORT "!api_port!" >nul 2>&1
    set "API_PORT=!api_port!"
    echo [✅] API port set to: !api_port!
)

:skip_env_setup
echo.
echo [📋] Current API Configuration:
if defined TAILSCALE_IP echo     • TAILSCALE_IP: %TAILSCALE_IP%
if defined TAILSCALE_SERVER_IP echo     • TAILSCALE_SERVER_IP: %TAILSCALE_SERVER_IP%
if defined API_PORT echo     • API_PORT: %API_PORT%
echo.

:: ——————————————————————————————————————————————
:: 7. Create Desktop Shortcut
:: ——————————————————————————————————————————————
echo [🔗] Creating desktop shortcut...
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT_PATH=%DESKTOP%\LoOper.lnk"
set "TARGET_PATH=%~dp0start.bat"
set "ICON_PATH=%~dp0LoOper.ico"

:: Create shortcut using PowerShell
powershell -Command "$WshShell = New-Object -comObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%TARGET_PATH%'; $Shortcut.IconLocation = '%ICON_PATH%'; $Shortcut.Save()"

:: Set shortcut to run as administrator using PowerShell
powershell -Command "$bytes = [System.IO.File]::ReadAllBytes('%SHORTCUT_PATH%'); $bytes[0x15] = $bytes[0x15] -bor 0x20; [System.IO.File]::WriteAllBytes('%SHORTCUT_PATH%', $bytes)"

if exist "%SHORTCUT_PATH%" (
    echo [✅] Desktop shortcut 'LoOper' created successfully!
    echo     • Location: %SHORTCUT_PATH%
    echo     • Target: %TARGET_PATH%
    echo     • Icon: %ICON_PATH%
    echo     • Run as Administrator: Enabled
) else (
    color 0E
    echo [⚠️] Warning: Desktop shortcut creation failed
)
echo.

:: ——————————————————————————————————————————————
:: 8. Launch LocalOperator
:: ——————————————————————————————————————————————
color 0B
echo.
echo     🚀 LAUNCHING LOCALOPERATOR 🚀
echo     ═══════════════════════════════
echo.
echo     📁 Working Directory: %CD%
echo     👨‍💻 Created by: RODRIGO DAVID BENITEZ
echo     🐍 Python Version: 3.13
echo     👁️ OCR Engine: Tesseract
echo     🌐 API Configuration: Ready
echo.
call :loading_animation "Starting LocalOperator application"

color 0A
echo [🎯] Running LocalOperator...
echo.
"%VENV_PY%" -m main
if errorlevel 1 (
    color 0C
    echo.
    echo     ❌ ERROR: LocalOperator execution failed!
    echo     ➤ Check the logs for more information
    echo.
    pause
    exit /b 1
)

color 0A
echo.
echo     ✅ SUCCESS! LocalOperator completed successfully!
echo     🎉 All systems operational under Python 3.13
echo.
echo     Press any key to exit...
pause >nul

:: Restore original directory
popd
exit /b 0

:: ——————————————————————————————————————————————
:: Loading Animation Function
:: ——————————————————————————————————————————————
:loading_animation
setlocal
set "message=%~1"
if "%message%"=="" set "message=Processing"

echo [⏳] %message%...
for /L %%i in (1,1,3) do (
    <nul set /p "=."
    timeout /t 1 /nobreak >nul 2>&1
)
echo.
goto :eof