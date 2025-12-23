# LoOper MSI Build Script
# This script generates a WiX configuration and builds the MSI installer.

$AppName = "LoOper"
$DistDir = Join-Path $PSScriptRoot "dist\LoOper"
$WxsFile = Join-Path $PSScriptRoot "LoOper.wxs"
$MsiFile = Join-Path $PSScriptRoot "LoOper.msi"

Write-Host "--- LoOper MSI Builder ---" -ForegroundColor Cyan

# 1. Verify dist directory
if (-not (Test-Path $DistDir)) {
    Write-Error "Directory $DistDir not found. Please compile the app first."
    exit 1
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
python generate_wxs.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to generate .wxs file."
    exit 1
}

# 4. Build MSI
if ($WixInstalled) {
    Write-Host "Building MSI..." -ForegroundColor Green
    wix build $WxsFile -ext WixToolset.UI.wixext -o $MsiFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Successfully created $MsiFile" -ForegroundColor Green
    } else {
        Write-Error "Failed to build MSI."
    }
} else {
    Write-Host "Please install WiX and run: wix build $WxsFile -o $MsiFile" -ForegroundColor Cyan
}

Write-Host "Done."
