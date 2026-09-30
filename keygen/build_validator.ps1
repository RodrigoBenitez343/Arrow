<#
.SYNOPSIS
    Build license_validator.exe from validate_license.py using PyInstaller.

    The HMAC secret is already embedded in the source code at compile time,
    so no RSA keys are needed.

    The resulting .exe is placed in dist/ and copied to dist/arrow/ for
    MSI bundling.
#>

param(
    [string]$KeygenDir = $PSScriptRoot,
    [string]$DistDir = (Join-Path $PSScriptRoot "dist"),
    [string]$PythonExe = "python"
)

$ValidatorSrc = Join-Path $KeygenDir "validate_license.py"
$ValidatorExe = Join-Path $DistDir "license_validator.exe"
$ArrowDistDir = Join-Path $DistDir "arrow"

Write-Host "--- Building license_validator.exe ---" -ForegroundColor Cyan

if (-not (Test-Path $ValidatorSrc)) {
    Write-Error "validate_license.py not found at $ValidatorSrc"
    exit 1
}

Write-Host "Running PyInstaller (this may take a while)..." -ForegroundColor Green
Push-Location $PSScriptRoot
& $PythonExe -m PyInstaller --onefile --noconsole --distpath $DistDir --name license_validator $ValidatorSrc
$BuildExit = $LASTEXITCODE
Pop-Location

# Clean up build artifacts
$SpecFile = Join-Path $PSScriptRoot "license_validator.spec"
if (Test-Path $SpecFile) { Remove-Item $SpecFile -Force }
$BuildDir = Join-Path $PSScriptRoot "build"
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force -ErrorAction SilentlyContinue }

if ($BuildExit -ne 0 -or -not (Test-Path $ValidatorExe)) {
    Write-Error "PyInstaller build failed."
    exit 1
}

Write-Host "Copying license_validator.exe to $ArrowDistDir..." -ForegroundColor Green
if (-not (Test-Path $ArrowDistDir)) {
    New-Item -ItemType Directory -Path $ArrowDistDir -Force | Out-Null
}
Copy-Item $ValidatorExe (Join-Path $ArrowDistDir "license_validator.exe") -Force

Write-Host "license_validator.exe built and copied successfully." -ForegroundColor Green
Write-Host "  Size: $((Get-Item $ValidatorExe).Length / 1KB) KB" -ForegroundColor Gray
exit 0
