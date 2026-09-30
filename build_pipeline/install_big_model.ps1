# install_big_model.ps1
# Post-install helper: copies the LocateAnything GGUF (3.4 GB - too large for
# MSI cabinets, so it ships OUTSIDE the .msi payload) into the app's models
# folder.  Invoked by the MSI custom action after InstallFinalize; the file is
# either next to the .msi in a Burn bundle temp dir ([SourceDir]) or beside
# the MSI in a folder install.  No-ops when the model was not shipped.
param(
    [string]$SourceDir = "",
    [string]$AppDir = ""
)
$ModelName = "LocateAnything-3B-Q8_0.gguf"
if (-not $SourceDir -or -not (Test-Path (Join-Path $SourceDir $ModelName))) {
    exit 0
}
if (-not $AppDir) {
    exit 0
}
$DstDir = Join-Path $AppDir "_internal\AI\models"
try {
    New-Item -ItemType Directory -Path $DstDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceDir $ModelName) (Join-Path $DstDir $ModelName) -Force
} catch {
    Write-Error "Failed to install $ModelName`: $_"
    exit 1
}
