@echo off
setlocal

:: ============================================================================
::  arrow / LoOper - one-shot bootstrap + launcher
:: ----------------------------------------------------------------------------
::  Clone the repo and run this file.  It will:
::    1. create the Python virtual environment and install dependencies
::    2. BUILD llama.cpp from utils/llama.cpp for this machine's architecture
::       (no prebuilt binaries are committed - they are produced here)
::    3. DOWNLOAD the base models + browser the app needs:
::         Laya decision engine, EmbeddingGemma, SmolLM3, LFM2.5-VL,
::         LocateAnything, PaddleOCR (det/rec/cls), Vosk STT, Piper TTS,
::         Chrome for Testing + matching chromedriver (web automation)
::    4. set up the RDP sandbox session
::    5. ask Debug vs Normal, then launch LoOper\main.py
:: ============================================================================

:: ---- Elevate to Administrator (the RDP sandbox needs it) -------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set "ROOT=%~dp0"
set "VENV_DIR=%ROOT%.venv"
set "PYTHON=python"
set "AI_BIN=%ROOT%LoOper\AI\bin"
set "AI_MODELS=%ROOT%LoOper\AI\models"
set "LAY_DIR=%AI_MODELS%\laya-GGUF"
set "LFM_DIR=%AI_MODELS%\LFM2.5-VL-450M-GGUF"
set "DATA_DIR=%ROOT%LoOper\data"
set "VOSK_DIR=%DATA_DIR%\vosk_models"
set "PIPER_DIR=%DATA_DIR%\piper_voices"

:: ============================================================================
::  1. Virtual environment + Python dependencies
:: ============================================================================
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo [setup] Creating virtual environment...
  "%PYTHON%" -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

echo [setup] Installing Python dependencies (pip install -e .)...
python -m pip install --upgrade pip
pushd "%ROOT%"
python -m pip install -e .
:: The editable install alone omits part of the runtime set (web automation,
:: piper-tts, pyqtgraph, lap).  Install the authoritative pinned lock too so a
:: fresh clone gets the exact environment the MSI build ships.
python -m pip install -r "%ROOT%build_pipeline\requirements.txt"
set "PIP_RC=%errorlevel%"
popd
if not "%PIP_RC%"=="0" (
  echo [error] pip install failed - fix the error above and re-run.
  pause
  exit /b 1
)

:: ============================================================================
::  2. Build llama.cpp for this machine
:: ============================================================================
call :build_llamacpp

:: ============================================================================
::  3. Download the base models (existing files are skipped)
:: ============================================================================
call :download_models

:: ============================================================================
::  4. RDP sandbox session setup
:: ============================================================================
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

:: ============================================================================
::  5. Launch - pick Debug (terminal visible) or Normal (no console)
:: ============================================================================
cd /d "%ROOT%"
echo.
echo   How do you want to start arrow?
echo     [D] Debug  - LoOper\main.py with the terminal visible (logs / tracebacks)
echo     [N] Normal - LoOper\main.py without a console window
choice /C DN /N /M "   Choose [D]ebug or [N]ormal: "
if errorlevel 2 goto launch_normal
goto launch_debug

:launch_debug
echo [run] Debug mode - terminal visible. Close this window to stop the app.
set "PYTHONFAULTHANDLER=1"
set "PYTHONUNBUFFERED=1"
"%VENV_DIR%\Scripts\python.exe" "%ROOT%LoOper\main.py"
echo.
echo [run] LoOper exited with code %errorlevel%.
pause
goto :eof

:launch_normal
echo [run] Normal mode - launching without a console window...
start "LoOper" "%VENV_DIR%\Scripts\pythonw.exe" "%ROOT%LoOper\main.py"
goto :eof


:: ============================================================================
::  :build_llamacpp - configure + build the vendored llama.cpp fork and stage
::  the resulting DLLs/EXEs into LoOper\AI\bin (binaries are never committed).
:: ============================================================================
:build_llamacpp
set "LLAMA_SRC=%ROOT%utils\llama.cpp"
set "LLAMA_BUILD=%LLAMA_SRC%\build"

if not exist "%LLAMA_SRC%\CMakeLists.txt" (
  echo [llama] Source not found at "%LLAMA_SRC%" - skipping build.
  exit /b 0
)

:: Already built? stage and done.
if exist "%LLAMA_BUILD%\bin\Release\llama-server.exe" (
  echo [llama] Existing build found - staging binaries.
  call :stage_llama "%LLAMA_BUILD%\bin\Release"
  exit /b 0
)
if exist "%LLAMA_BUILD%\bin\llama-server.exe" (
  echo [llama] Existing build found - staging binaries.
  call :stage_llama "%LLAMA_BUILD%\bin"
  exit /b 0
)

where cmake >nul 2>&1
if errorlevel 1 (
  echo [llama] CMake not found - cannot build local inference.
  echo         Install "CMake" and "Visual Studio Build Tools ^(Desktop C++^)",
  echo         then re-run this script. The app can also fall back to Ollama.
  exit /b 0
)

:: Backends: enable Vulkan when the SDK is present, else CPU-only build.
set "BACKEND_FLAGS="
if defined VULKAN_SDK (
  echo [llama] Vulkan SDK detected - enabling the Vulkan backend.
  set "BACKEND_FLAGS=-DGGML_VULKAN=ON"
) else (
  echo [llama] No Vulkan SDK - building CPU-only (works everywhere).
)

:: Architecture: x64 (default) or arm64.
set "ARCH_FLAG="
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "ARCH_FLAG=-A arm64"

echo [llama] Configuring...
cmake -S "%LLAMA_SRC%" -B "%LLAMA_BUILD%" -DCMAKE_BUILD_TYPE=Release %BACKEND_FLAGS% %ARCH_FLAG% -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
if errorlevel 1 (
  echo [llama] CMake configure failed - skipping build.
  exit /b 0
)

echo [llama] Building (this can take several minutes)...
cmake --build "%LLAMA_BUILD%" --config Release --parallel
if errorlevel 1 echo [llama] Build reported errors - continuing.

if exist "%LLAMA_BUILD%\bin\Release\llama-server.exe" call :stage_llama "%LLAMA_BUILD%\bin\Release"
if exist "%LLAMA_BUILD%\bin\llama-server.exe"         call :stage_llama "%LLAMA_BUILD%\bin"
exit /b 0

:stage_llama
if not exist "%AI_BIN%" mkdir "%AI_BIN%" >nul 2>&1
echo [llama] Staging EXE/DLL from %~1 -^> %AI_BIN%
for %%F in ("%~1\*.exe") do copy /Y "%%~F" "%AI_BIN%" >nul 2>&1
for %%F in ("%~1\*.dll") do copy /Y "%%~F" "%AI_BIN%" >nul 2>&1
exit /b 0


:: ============================================================================
::  :download_models - fetch the base models the app needs
:: ============================================================================
:download_models
echo.
echo [models] Fetching base models...

:: -- Laya System-1 decision engine (models + engine binary) ------------------
:: NOTE: every Hugging Face download below is pinned to a specific revision
:: (commit sha) so an upstream repo update can never silently change the model.
call :getfile "https://huggingface.co/mys/laya-GGUF/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_ud_q4_k_m.gguf" "%LAY_DIR%\laya_english_ud_q4_k_m.gguf"
call :getfile "https://huggingface.co/mys/laya-GGUF/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_q8_0.gguf" "%LAY_DIR%\laya_english_q8_0.gguf"
call :get_laya_exe

:: -- EmbeddingGemma (offline embeddings / RAG) --------------------------------
call :getfile "https://huggingface.co/unsloth/embeddinggemma-300m-GGUF/resolve/6661a6504c30d8304af13455cb4a5d4f5bc6011f/embeddinggemma-300M-Q8_0.gguf" "%AI_MODELS%\embeddinggemma-300M-Q8_0.gguf"

:: -- SmolLM3 (default chat / chain-router model) ------------------------------
call :getfile "https://huggingface.co/ggml-org/SmolLM3-3B-GGUF/resolve/4965cb60b150737b68a0408c36aeefb65078f894/SmolLM3-Q4_K_M.gguf" "%AI_MODELS%\SmolLM3-Q4_K_M.gguf"

:: -- LFM2.5-VL (vision understanding) + vision projector ----------------------
call :getfile "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/LFM2.5-VL-450M-Q8_0.gguf" "%LFM_DIR%\LFM2.5-VL-450M-Q8_0.gguf"
call :getfile "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/mmproj-LFM2.5-VL-450m-Q8_0.gguf" "%LFM_DIR%\mmproj-LFM2.5-VL-450m-Q8_0.gguf"

:: -- LocateAnything (UI grounding / Handle node) + projector ------------------
call :getfile "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF/resolve/aac6aefe07703a6c994fab05828cfa5524609380/locateanything-3b-q8_0.gguf" "%AI_MODELS%\LocateAnything-3B-Q8_0.gguf"
call :getfile "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF/resolve/aac6aefe07703a6c994fab05828cfa5524609380/mmproj-locateanything-3b-bf16.gguf" "%AI_MODELS%\mmproj-LocateAnything-3B-BF16.gguf"

:: -- PaddleOCR models, Vosk STT model, Piper TTS voices -----------------------
call :get_paddle_models
call :get_vosk_model
call :get_piper_voices

:: -- Chrome for Testing + matching chromedriver (web automation) --------------
call :get_chrome

echo [models] Base models ready.
exit /b 0


:: ---- Laya engine binary (ggmlc release; Vulkan build with CPU fallback) -----
:get_laya_exe
if exist "%AI_BIN%\laya.exe" (
  echo   [skip] %AI_BIN%\laya.exe
  exit /b 0
)
call :getfile "https://github.com/monatis/ggmlc/releases/download/v0.9.6/laya-windows-x86_64-vulkan.zip" "%TEMP%\laya-win.zip"
if not exist "%TEMP%\laya-win.zip" exit /b 0
if not exist "%AI_BIN%" mkdir "%AI_BIN%" >nul 2>&1
echo   [pkg ] extracting laya.exe
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=Join-Path $env:TEMP 'laya_extract';" ^
  "if (Test-Path $d) { Remove-Item $d -Recurse -Force };" ^
  "Expand-Archive -Path (Join-Path $env:TEMP 'laya-win.zip') -DestinationPath $d -Force;" ^
  "$exe=Get-ChildItem -Path $d -Recurse -Filter 'laya.exe' | Select-Object -First 1;" ^
  "if ($exe) { Copy-Item $exe.FullName '%AI_BIN%\laya.exe' -Force }"
exit /b 0


:: ---- Vosk STT model --------------------------------------------------------
:get_vosk_model
if exist "%VOSK_DIR%\vosk-model-small-en-us-0.15" (
  echo   [skip] %VOSK_DIR%\vosk-model-small-en-us-0.15
  exit /b 0
)
call :getfile "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" "%TEMP%\vosk-model-small-en-us-0.15.zip"
if not exist "%TEMP%\vosk-model-small-en-us-0.15.zip" exit /b 0
echo   [pkg ] extracting Vosk model
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Expand-Archive -Path (Join-Path $env:TEMP 'vosk-model-small-en-us-0.15.zip') -DestinationPath '%VOSK_DIR%' -Force"
exit /b 0


:: ---- Piper TTS voices ------------------------------------------------------
:get_piper_voices
call :getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" "%PIPER_DIR%\en_US-lessac-medium.onnx"
call :getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" "%PIPER_DIR%\en_US-lessac-medium.onnx.json"
call :getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx" "%PIPER_DIR%\es_ES-carlfm-x_low.onnx"
call :getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx.json" "%PIPER_DIR%\es_ES-carlfm-x_low.onnx.json"
exit /b 0


:: ---- Chrome for Testing + matching chromedriver (web automation) ------------
:: Deterministic browser for the WebSequence nodes (no dependence on a system
:: Chrome).  Lands as the bundle the app/spec expect:
::   LoOper\AI\bin\chrome\chrome.exe  +  chromedriver.exe  +  .chrome-version
:get_chrome
set "CHROME_DIR=%AI_BIN%\chrome"
if exist "%CHROME_DIR%\chrome.exe" if exist "%CHROME_DIR%\chromedriver.exe" (
  echo   [skip] %CHROME_DIR%
  exit /b 0
)

:: PINNED version - undetected_chromedriver requires the Chrome binary and the
:: chromedriver to match EXACTLY, and this is the version the web nodes are
:: tested against.  Bump this one line to upgrade both together.
set "CFT_VER=153.0.8010.48"
echo   [info] Chrome for Testing %CFT_VER% ^(pinned^)
set "CFT_BASE=https://storage.googleapis.com/chrome-for-testing-public/%CFT_VER%/win64"

call :getfile "%CFT_BASE%/chrome-win64.zip" "%TEMP%\cft-chrome-win64.zip"
if not exist "%TEMP%\cft-chrome-win64.zip" (
  echo   [warn] Chrome for Testing download failed - skipping.
  exit /b 0
)
call :getfile "%CFT_BASE%/chromedriver-win64.zip" "%TEMP%\cft-chromedriver-win64.zip"

if not exist "%AI_BIN%" mkdir "%AI_BIN%" >nul 2>&1
echo   [pkg ] extracting Chrome for Testing
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=Join-Path $env:TEMP 'cft_chrome'; if (Test-Path $d) { Remove-Item $d -Recurse -Force };" ^
  "Expand-Archive -Path (Join-Path $env:TEMP 'cft-chrome-win64.zip') -DestinationPath $d -Force;" ^
  "$dir='%CHROME_DIR%'; if (Test-Path $dir) { Get-ChildItem $dir -Force | Remove-Item -Recurse -Force } else { New-Item -ItemType Directory -Force -Path $dir | Out-Null };" ^
  "Copy-Item -Path (Join-Path $d 'chrome-win64\*') -Destination $dir -Recurse -Force;"

if exist "%TEMP%\cft-chromedriver-win64.zip" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$d=Join-Path $env:TEMP 'cft_driver'; if (Test-Path $d) { Remove-Item $d -Recurse -Force };" ^
    "Expand-Archive -Path (Join-Path $env:TEMP 'cft-chromedriver-win64.zip') -DestinationPath $d -Force;" ^
    "Copy-Item -Path (Join-Path $d 'chromedriver-win64\chromedriver.exe') -Destination (Join-Path '%CHROME_DIR%' 'chromedriver.exe') -Force;"
)
> "%CHROME_DIR%\.chrome-version" echo %CFT_VER%
echo   [pkg ] Chrome for Testing staged at %CHROME_DIR%
exit /b 0


:: ---- PaddleOCR det/rec/cls models -> %USERPROFILE%\.paddleocr\whl -----------
:get_paddle_models
set "PADDLE_WHL=%USERPROFILE%\.paddleocr\whl"
set "PD_DET=%PADDLE_WHL%\det\en\en_PP-OCRv3_det_infer"
set "PD_REC=%PADDLE_WHL%\rec\en\en_PP-OCRv4_rec_infer"
set "PD_CLS=%PADDLE_WHL%\cls\ch_ppocr_mobile_v2.0_cls_infer"

if not exist "%PD_DET%" (
  call :getfile "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar" "%TEMP%\en_PP-OCRv3_det_infer.tar"
  if exist "%TEMP%\en_PP-OCRv3_det_infer.tar" (
    if not exist "%PADDLE_WHL%\det\en" mkdir "%PADDLE_WHL%\det\en" >nul 2>&1
    echo   [pkg ] extracting en_PP-OCRv3_det_infer
    tar -xf "%TEMP%\en_PP-OCRv3_det_infer.tar" -C "%PADDLE_WHL%\det\en"
  )
)
if not exist "%PD_REC%" (
  call :getfile "https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar" "%TEMP%\en_PP-OCRv4_rec_infer.tar"
  if exist "%TEMP%\en_PP-OCRv4_rec_infer.tar" (
    if not exist "%PADDLE_WHL%\rec\en" mkdir "%PADDLE_WHL%\rec\en" >nul 2>&1
    echo   [pkg ] extracting en_PP-OCRv4_rec_infer
    tar -xf "%TEMP%\en_PP-OCRv4_rec_infer.tar" -C "%PADDLE_WHL%\rec\en"
  )
)
if not exist "%PD_CLS%" (
  call :getfile "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar" "%TEMP%\ch_ppocr_mobile_v2.0_cls_infer.tar"
  if exist "%TEMP%\ch_ppocr_mobile_v2.0_cls_infer.tar" (
    if not exist "%PADDLE_WHL%\cls" mkdir "%PADDLE_WHL%\cls" >nul 2>&1
    echo   [pkg ] extracting ch_ppocr_mobile_v2.0_cls_infer
    tar -xf "%TEMP%\ch_ppocr_mobile_v2.0_cls_infer.tar" -C "%PADDLE_WHL%\cls"
  )
)
exit /b 0


:: ============================================================================
::  :getfile <url> <dest>  - resumable download, skipped when dest is present
:: ============================================================================
:getfile
if exist "%~2" (
  for %%A in ("%~2") do if %%~zA GTR 0 (
    echo   [skip] %~nx2
    exit /b 0
  )
)
echo   [get ] %~2
if not exist "%~dp2." mkdir "%~dp2" >nul 2>&1
where curl >nul 2>&1
if not errorlevel 1 (
  curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o "%~2" "%~1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }"
)
if errorlevel 1 (
  echo   [warn] download failed: %~1
  del /q "%~2" >nul 2>&1
)
exit /b 0
