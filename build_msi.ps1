# arrow MSI Build Script
# This script generates a WiX configuration and builds the MSI installer.

$AppName = "arrow"
$DistDir = Join-Path $PSScriptRoot "dist\arrow"
$WxsFile = Join-Path $PSScriptRoot "arrow.wxs"
$MsiFile = Join-Path $PSScriptRoot "arrow.msi"
$SpecFile = Join-Path $PSScriptRoot "LoOperApp.spec"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$AppExe = Join-Path $DistDir "arrow.exe"
$ReqFile = Join-Path $PSScriptRoot "requirements.txt"

Write-Host "--- arrow MSI Builder ---" -ForegroundColor Cyan

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Green
    Push-Location $PSScriptRoot
    python -m venv ".venv"
    $VenvExitCode = $LASTEXITCODE
    Pop-Location
    if ($VenvExitCode -ne 0 -or -not (Test-Path $VenvPython)) {
        Write-Error "Failed to create .venv"
        exit 1
    }
    $PythonExe = $VenvPython
}

if (Test-Path $ReqFile) {
    Write-Host "Installing Python dependencies into .venv..." -ForegroundColor Green
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install Python dependencies from $ReqFile"
        exit 1
    }
}

if (-not (Test-Path $AppExe)) {
    if (-not (Test-Path $SpecFile)) {
        Write-Error "Spec file not found: $SpecFile"
        exit 1
    }
    Write-Host "Building arrow executable (PyInstaller)..." -ForegroundColor Green
    Push-Location $PSScriptRoot
    & $PythonExe -m PyInstaller -y $SpecFile
    $BuildExitCode = $LASTEXITCODE
    Pop-Location
    if ($BuildExitCode -ne 0 -or -not (Test-Path $AppExe)) {
        Write-Error "Failed to build arrow.exe into $DistDir"
        exit 1
    }
}

# 2.5. Build license validator
& "$PSScriptRoot\keygen\build_validator.ps1" -PythonExe $PythonExe -DistDir (Join-Path $PSScriptRoot "dist")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to build license_validator.exe"
    exit 1
}

# 2.6. Bundle llama.cpp binaries
Write-Host "Bundling llama.cpp binaries..." -ForegroundColor Green
$LlamaBinDir = Join-Path $PSScriptRoot "utils\llama.cpp\build\bin\Release"
$LlamaDestDir = Join-Path $DistDir "bin"
$NeededBinaries = @("llama-server.exe", "llama-mtmd-cli.exe")

if (Test-Path $LlamaBinDir) {
    if (-not (Test-Path $LlamaDestDir)) {
        New-Item -ItemType Directory -Path $LlamaDestDir -Force | Out-Null
    }
    foreach ($exe in $NeededBinaries) {
        $src = Join-Path $LlamaBinDir $exe
        if (Test-Path $src) {
            $dst = Join-Path $LlamaDestDir $exe
            Copy-Item $src $dst -Force
            $size = (Get-Item $dst).Length / 1MB
            Write-Host "  Bundled $exe ($([math]::Round($size, 1)) MB)" -ForegroundColor Gray
        } else {
            Write-Warning "llama.cpp binary not found: $src"
            Write-Warning "Build llama.cpp first: cd utils\llama.cpp && cmake --build build --config Release"
        }
    }
} else {
    Write-Warning "llama.cpp build directory not found at $LlamaBinDir"
    Write-Warning "Skipping llama.cpp binary bundling."
}

# 3. Check for WiX Toolset
Write-Host "Checking for WiX Toolset..."
$WixInstalled = Get-Command wix -ErrorAction SilentlyContinue

if (-not $WixInstalled) {
    Write-Host "WiX Toolset (v4+) not found." -ForegroundColor Yellow
    Write-Host "You can install it using dotnet tool:"
    Write-Host "  dotnet tool install --global wix"
    Write-Host ""
    Write-Host "If you don't have .NET SDK, please install it from: https://dotnet.microsoft.com/download"
    Write-Host "Or download WiX binaries from: https://wixtoolset.org/releases/"
    
    $Choice = Read-Host "Would you like to try installing WiX now? (Y/N)"
    if ($Choice -eq 'Y' -or $Choice -eq 'y') {
        dotnet tool install --global wix
        $WixInstalled = Get-Command wix -ErrorAction SilentlyContinue
    }
}

if (-not $WixInstalled) {
    Write-Warning "WiX not found. I will still generate the .wxs file for you."
}

# 3. Generate .wxs file using Python
Write-Host "Generating WiX source file (.wxs)..."
& $PythonExe generate_wxs.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to generate .wxs file."
    exit 1
}

# 4. Build MSI
if ($WixInstalled) {
    Write-Host "Building MSI..." -ForegroundColor Green
    wix eula accept wix7 *> $null
    wix extension add -g WixToolset.UI.wixext *> $null
    wix build -acceptEula wix7 -arch x64 $WxsFile -ext WixToolset.UI.wixext -o $MsiFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Successfully created $MsiFile" -ForegroundColor Green
    } else {
        Write-Error "Failed to build MSI."
    }
} else {
    Write-Host "Please install WiX and run: wix build $WxsFile -o $MsiFile" -ForegroundColor Cyan
}

Write-Host "Done."
