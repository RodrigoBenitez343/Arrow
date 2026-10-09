# install_dependencies.ps1
# Post-install step: delegates ALL asset provisioning (GGUF models + engine
# utilities such as llama.cpp binaries, laya.exe, Chrome, Vosk, Piper,
# PaddleOCR) to the app's own lazy worker via `arrow.exe --install-deps`.
# That worker checks what is present and fetches the rest one at a time.

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Windows.Forms

function Show-Notification($Title, $Text) {
    $balloon = New-Object System.Windows.Forms.NotifyIcon
    $balloon.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Get-Process -Id $PID).Path)
    $balloon.BalloonTipIcon = "Info"
    $balloon.BalloonTipText = $Text
    $balloon.BalloonTipTitle = $Title
    $balloon.Visible = $true
    $balloon.ShowBalloonTip(5000)
    return $balloon
}

# ---------------------------------------------------------------------------
# 0. Build/verify the llama.cpp engine FOR THIS HOST.
#    Nothing ISA-specific ships in the MSI: setup_llama_engine.ps1 detects the
#    host, installs the C++ toolchain when a local build is required, and
#    compiles the engine in place (falling back to a portable prebuilt).  This
#    is what stops an engine built on a developer machine from conflicting with
#    the end user's CPU.
# ---------------------------------------------------------------------------
# The llama.cpp source ships as ONE zip (a single MSI Component instead of
# ~38k loose files, which would blow the 65536-Component WIX7502 cap).  Unpack
# it beside the app so the engine script can build from source as before.
$LlamaSrcZip = Join-Path $PSScriptRoot "_internal\llama.cpp.zip"
$LlamaSrcDir = Join-Path $PSScriptRoot "_internal\llama.cpp"
if ((Test-Path $LlamaSrcZip) -and -not (Test-Path (Join-Path $LlamaSrcDir "CMakeLists.txt"))) {
    New-Item -ItemType Directory -Force -Path $LlamaSrcDir | Out-Null
    tar -xf $LlamaSrcZip -C $LlamaSrcDir
}

$EngineScript = Join-Path $PSScriptRoot "setup_llama_engine.ps1"
if (Test-Path $EngineScript) {
    & $EngineScript -BinDest (Join-Path $PSScriptRoot "_internal\bin") `
                    -SourceDir $LlamaSrcDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "llama.cpp engine provisioning did not complete; models will still be fetched."
    }
} else {
    Write-Warning "setup_llama_engine.ps1 not found next to the app - skipping the engine build."
}

# ---------------------------------------------------------------------------
# 1. Provision EVERY runtime asset (OCR models, GGUF models, engine utilities)
#    via the app's own lazy worker.  `arrow.exe --install-deps` runs the SAME
#    provisioner the app uses at startup - one asset at a time, skipping what
#    is already present, then exits.  All download logic lives in ONE place
#    (LoOper/AI/model_downloads.py) instead of being duplicated here.
# ---------------------------------------------------------------------------
$LoOperExe = Join-Path $PSScriptRoot "arrow.exe"
if (-not (Test-Path $LoOperExe)) {
    $LoOperExe = Join-Path $PSScriptRoot "LoOper.exe"
}
if (Test-Path $LoOperExe) {
    $balloon = Show-Notification "arrow Setup" "Verifying AI models and utilities..."
    try {
        Start-Process -FilePath $LoOperExe -ArgumentList "--install-deps" -Wait -NoNewWindow
    } catch {
        # Ignore errors - the app re-runs provisioning on next launch anyway.
    } finally {
        $balloon.Dispose()
    }
}
