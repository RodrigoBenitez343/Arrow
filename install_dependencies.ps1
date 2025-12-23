# install_dependencies.ps1
# This script checks for Tesseract OCR and Ollama, and installs them if missing.

Add-Type -AssemblyName PresentationFramework

function Show-MessageBox($Message, $Title, $Buttons = "YesNo", $Icon = "Question") {
    return [System.Windows.MessageBox]::Show($Message, $Title, $Buttons, $Icon)
}

$TesseractInstalled = $false
$OllamaInstalled = $false

# 1. Check Tesseract
if (Get-Command tesseract -ErrorAction SilentlyContinue) {
    $TesseractInstalled = $true
} elseif (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe") {
    $TesseractInstalled = $true
}

# 2. Check Ollama
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $OllamaInstalled = $true
} elseif (Test-Path "$env:LOCALAPPDATA\Ollama\ollama.exe") {
    $OllamaInstalled = $true
}

if (-not $TesseractInstalled -or -not $OllamaInstalled) {
    # If we are running from the MSI, we might want to be more silent or just show progress
    # But since we want the user to "see" it, we'll keep the message box for confirmation if needed
    # or just proceed if the user already agreed in the MSI (though we don't have that link yet)
    
    $Missing = @()
    if (-not $TesseractInstalled) { $Missing += "Tesseract OCR" }
    if (-not $OllamaInstalled) { $Missing += "Ollama" }
    
    $MissingStr = $Missing -join " and "
    $Msg = "LoOper is now installing $MissingStr. This may take a few minutes depending on your internet connection."
    
    # We'll show a small progress window instead of just a message box if possible
    # For now, let's just use a non-blocking notification or a simple tray balloon
    
    Add-Type -AssemblyName System.Windows.Forms
    $balloon = New-Object System.Windows.Forms.NotifyIcon
    $balloon.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Get-Process -Id $PID).Path)
    $balloon.BalloonTipIcon = "Info"
    $balloon.BalloonTipText = "Downloading and installing $MissingStr..."
    $balloon.BalloonTipTitle = "LoOper Setup"
    $balloon.Visible = $true
    $balloon.ShowBalloonTip(5000)

    if (-not $TesseractInstalled) {
            Write-Host "Downloading Tesseract..."
            $TessUrl = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe"
            $TessPath = Join-Path $env:TEMP "tesseract-setup.exe"
            try {
                if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                    curl.exe -L $TessUrl -o $TessPath
                } else {
                    Invoke-WebRequest -Uri $TessUrl -OutFile $TessPath
                }
                Write-Host "Installing Tesseract..."
                Start-Process -FilePath $TessPath -ArgumentList "/S" -Wait
            } catch {
                Show-MessageBox "Failed to download or install Tesseract: $($_.Exception.Message)" "Error" "OK" "Error"
            } finally {
                if (Test-Path $TessPath) { Remove-Item $TessPath }
            }
        }
        
        if (-not $OllamaInstalled) {
            Write-Host "Downloading Ollama..."
            $OllamaUrl = "https://ollama.com/download/OllamaSetup.exe"
            $OllamaPath = Join-Path $env:TEMP "OllamaSetup.exe"
            try {
                if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                    curl.exe -L $OllamaUrl -o $OllamaPath
                } else {
                    Invoke-WebRequest -Uri $OllamaUrl -OutFile $OllamaPath
                }
                Write-Host "Installing Ollama..."
                # Ollama setup /silent installs it in the background
                Start-Process -FilePath $OllamaPath -ArgumentList "/silent" -Wait
            } catch {
                Show-MessageBox "Failed to download or install Ollama: $($_.Exception.Message)" "Error" "OK" "Error"
            } finally {
                if (Test-Path $OllamaPath) { Remove-Item $OllamaPath }
            }
        }
        $balloon.BalloonTipText = "Dependencies installed successfully."
        $balloon.ShowBalloonTip(5000)
        Sleep 5
        $balloon.Dispose()
    }
}
