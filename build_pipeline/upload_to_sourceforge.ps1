# upload_to_sourceforge.ps1
# Uploads a release file to a SourceForge project's Files (FRS) area over SFTP.
#
# Usage (from PowerShell):
#   .\upload_to_sourceforge.ps1
#   .\upload_to_sourceforge.ps1 -LocalZip "d:\path\to\other.zip" -RemoteFolder "v1.0"
#
# Notes:
#   * SourceForge SFTP login format is  username,projectname@frs.sourceforge.net  (comma required).
#   * The script asks for your SourceForge *shell* password interactively. No password is stored.
#   * sftp/scp cannot resume a broken 1.2 GB transfer. For flaky links use rsync -P instead
#     (see the comment block at the bottom of this file).

param(
    [string]$SfUser       = 'rodrigobenitez3',
    [string]$Project      = 'arrowlocalbot',
    [string]$LocalZip     = 'd:\LoOperV2\build_pipeline\Arrow_setup.zip',
    [string]$RemoteFolder = 'Arrow_setup'
)

$ErrorActionPreference = 'Stop'

# --- Pre-flight checks -------------------------------------------------------
if (-not (Get-Command sftp -ErrorAction SilentlyContinue)) {
    throw "sftp not found. It ships with Windows OpenSSH; enable it via 'Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0'."
}
if (-not (Test-Path -LiteralPath $LocalZip)) {
    throw "Local file not found: $LocalZip"
}

$item     = Get-Item -LiteralPath $LocalZip
$name     = $item.Name
$sizeMB   = [math]::Round($item.Length / 1MB, 2)
$sfHost   = "$SfUser,$Project@frs.sourceforge.net"
$basePath = "/home/frs/project/$Project"
# sftp on Windows accepts forward slashes for the local path.
$localPut = ($item.FullName -replace '\\', '/')

Write-Host ""
Write-Host "SourceForge upload" -ForegroundColor Cyan
Write-Host "  file   : $($item.FullName)  ($sizeMB MB)"
Write-Host "  host   : $sfHost"
Write-Host "  remote : $basePath/$RemoteFolder/$name"
Write-Host ""

# --- Build the sftp batch file ----------------------------------------------
# A leading '-' on a command makes sftp ignore errors for it instead of aborting,
# so a pre-existing remote folder (mkdir failure) does not stop the upload.
$batch = @"
cd $basePath
-mkdir $RemoteFolder
cd $RemoteFolder
put "$localPut" "$name"
ls -l
"@

$batchFile = Join-Path $env:TEMP ("sftp_batch_{0}.txt" -f ([guid]::NewGuid().ToString('N')))
# ASCII (no BOM) so the remote sftp does not choke on a byte-order mark.
Set-Content -LiteralPath $batchFile -Value $batch -Encoding ASCII

Write-Host "Connecting... enter your SourceForge shell password when prompted." -ForegroundColor Yellow
Write-Host "(If this is your first SFTP use, set the password at https://sourceforge.net/auth/shell_services)" -ForegroundColor DarkGray

try {
    # accept-new auto-accepts a first-seen host key; batch mode cannot answer the
    # interactive "continue connecting?" prompt, which otherwise fails with code 255.
    sftp -b $batchFile -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 $sfHost
    $code = $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $batchFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($code -ne 0) {
    throw "sftp exited with code $code. Upload may have failed; re-run to retry."
}

Write-Host "Upload finished. The 'ls -l' output above should list $name at ~$sizeMB MB." -ForegroundColor Green
Write-Host "Verify in the browser (allow ~1 min for processing):" -ForegroundColor Green
Write-Host "  https://sourceforge.net/projects/$Project/files/$RemoteFolder/" -ForegroundColor Green
Write-Host ""
Write-Host "Direct download link (once it appears):" -ForegroundColor Green
Write-Host "  https://sourceforge.net/projects/$Project/files/$RemoteFolder/$name/download" -ForegroundColor Green

# --- Resumable alternative (for flaky connections) --------------------------
# rsync -P resumes instead of restarting. Needs rsync (Git Bash / WSL / cwRsync):
#   rsync -avP "$localPut" "$SfUser@frs.sourceforge.net:$basePath/$RemoteFolder/"
