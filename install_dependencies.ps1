# install_dependencies.ps1
# This script checks for Ollama and PaddleOCR models, and installs them if missing.

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

$OllamaInstalled = $false
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $OllamaInstalled = $true
} elseif (Test-Path "$env:LOCALAPPDATA\Ollama\ollama.exe") {
    $OllamaInstalled = $true
}

if (-not $OllamaInstalled) {
    $balloon = Show-Notification "LoOper Setup" "Downloading and installing Ollama..."
    
    try {
        $OllamaUrl = "https://ollama.com/download/OllamaSetup.exe"
        $OllamaPath = Join-Path $env:TEMP "OllamaSetup.exe"
        
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            curl.exe -L $OllamaUrl -o $OllamaPath
        } else {
            Invoke-WebRequest -Uri $OllamaUrl -OutFile $OllamaPath
        }
        
        Start-Process -FilePath $OllamaPath -ArgumentList "/silent" -Wait
    } catch {
        # Silent fail or log
    } finally {
        if (Test-Path $OllamaPath) { Remove-Item $OllamaPath }
        $balloon.Dispose()
    }
}

# Always run PaddleOCR check
# We use the LoOper executable itself to trigger the download using the bundled library
$LoOperExe = Join-Path $PSScriptRoot "LoOper.exe"
if (Test-Path $LoOperExe) {
    $balloon = Show-Notification "LoOper Setup" "Verifying AI models (OCR)..."
    try {
        # Run LoOper with --install-deps to trigger model download
        # We use Start-Process to ensure we can wait for it
        Start-Process -FilePath $LoOperExe -ArgumentList "--install-deps" -Wait -NoNewWindow
    } catch {
        # Ignore errors
    } finally {
        $balloon.Dispose()
    }
}
