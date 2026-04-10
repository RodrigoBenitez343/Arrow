# LoOper MSI Build Script
# This script generates a WiX configuration and builds the MSI installer.

$AppName = "LoOper"
$DistDir = Join-Path $PSScriptRoot "dist\LoOper"
$WxsFile = Join-Path $PSScriptRoot "LoOper.wxs"
$MsiFile = Join-Path $PSScriptRoot "LoOper.msi"
$SpecFile = Join-Path $PSScriptRoot "LoOperApp.spec"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$AppExe = Join-Path $DistDir "LoOper.exe"
$ReqFile = Join-Path $PSScriptRoot "requirements.txt"

Write-Host "--- LoOper MSI Builder ---" -ForegroundColor Cyan

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
    Write-Host "Building LoOper executable (PyInstaller)..." -ForegroundColor Green
    Push-Location $PSScriptRoot
    & $PythonExe -m PyInstaller -y $SpecFile
    $BuildExitCode = $LASTEXITCODE
    Pop-Location
    if ($BuildExitCode -ne 0 -or -not (Test-Path $AppExe)) {
        Write-Error "Failed to build LoOper.exe into $DistDir"
        exit 1
    }
}

# 2. Check for WiX Toolset
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
