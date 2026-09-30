# install_dependencies.ps1
# This script verifies PaddleOCR models and downloads llama.cpp models,
# installing or downloading them if missing.

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
# 1. PaddleOCR model verification
# ---------------------------------------------------------------------------
$LoOperExe = Join-Path $PSScriptRoot "arrow.exe"
if (-not (Test-Path $LoOperExe)) {
    $LoOperExe = Join-Path $PSScriptRoot "LoOper.exe"
}
if (Test-Path $LoOperExe) {
    $balloon = Show-Notification "arrow Setup" "Verifying AI models (OCR)..."
    try {
        Start-Process -FilePath $LoOperExe -ArgumentList "--install-deps" -Wait -NoNewWindow
    } catch {
        # Ignore errors
    } finally {
        $balloon.Dispose()
    }
}

# ---------------------------------------------------------------------------
# 2. llama.cpp model download
# ---------------------------------------------------------------------------
$LlamaBinDir = Join-Path $PSScriptRoot "bin"
$MtmdCli = Join-Path $LlamaBinDir "llama-mtmd-cli.exe"
$ModelsDir = Join-Path $PSScriptRoot "AI" "models"
$LfmDir = Join-Path $ModelsDir "LFM2.5-VL-450M-GGUF"

# Check if llama-mtmd-cli is available (bundled via MSI)
$LlamaCppAvailable = Test-Path $MtmdCli
if (-not $LlamaCppAvailable) {
    # Also check PATH as fallback
    $MtmdCli = (Get-Command "llama-mtmd-cli" -ErrorAction SilentlyContinue).Source
    $LlamaCppAvailable = ($null -ne $MtmdCli)
}

if ($LlamaCppAvailable) {
    # Determine which LFM quant to download
    # Prefer Q8_0, fall back to Q4_0
    $model_quant = "Q8_0"
    $mmproj_quant = "Q8_0"
    
    # Check if models already exist
    $model_file = Join-Path $LfmDir "LFM2.5-VL-450M-$model_quant.gguf"
    $mmproj_file = Join-Path $LfmDir "mmproj-LFM2.5-VL-450m-$mmproj_quant.gguf"
    
    $need_model = -not (Test-Path $model_file)
    $need_mmproj = -not (Test-Path $mmproj_file)
    
    if ($need_model -or $need_mmproj) {
        $balloon = Show-Notification "arrow Setup" "Downloading AI models (LFM vision)..."
        try {
            if (-not (Test-Path $LfmDir)) {
                New-Item -ItemType Directory -Path $LfmDir -Force | Out-Null
            }
            
            Write-Host "Downloading LFM2.5-VL-450M-GGUF models (this may take a while)..."
            
            if ($need_model) {
                Write-Host "  Downloading LFM2.5-VL-450M-$model_quant.gguf..."
                & $MtmdCli -hf LiquidAI/LFM2.5-VL-450M-GGUF:$model_quant --models-dir $LfmDir
            }
            
            if ($need_mmproj) {
                Write-Host "  Downloading mmproj-LFM2.5-VL-450m-$mmproj_quant.gguf..."
                & $MtmdCli -hf LiquidAI/LFM2.5-VL-450M-GGUF --mmproj $mmproj_quant --models-dir $LfmDir
            }
            
            Write-Host "LFM vision model download complete." -ForegroundColor Green
        } catch {
            Write-Warning "Failed to download LFM models via mtmd-cli. Trying direct download..."
            try {
                # Fallback: direct Hugging Face download
                $hf_base = "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF/resolve/main"
                if ($need_model) {
                    $url = "$hf_base/LFM2.5-VL-450M-$model_quant.gguf"
                    $out = Join-Path $LfmDir "LFM2.5-VL-450M-$model_quant.gguf"
                    Write-Host "  Downloading $url..."
                    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
                }
                if ($need_mmproj) {
                    $url = "$hf_base/mmproj-LFM2.5-VL-450m-$mmproj_quant.gguf"
                    $out = Join-Path $LfmDir "mmproj-LFM2.5-VL-450m-$mmproj_quant.gguf"
                    Write-Host "  Downloading $url..."
                    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
                }
                Write-Host "Direct download complete." -ForegroundColor Green
            } catch {
                Write-Warning "Direct download also failed: $_"
            }
        } finally {
            $balloon.Dispose()
        }
    } else {
        Write-Host "LFM vision models already present." -ForegroundColor Gray
    }
} else {
    Write-Warning "llama-mtmd-cli not found. Skipping LFM model download."
    Write-Warning "LFM models can be downloaded later from: https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF"
}
