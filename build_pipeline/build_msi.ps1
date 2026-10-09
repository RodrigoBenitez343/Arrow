# arrow MSI Build Script
# This script generates a WiX configuration and builds the MSI installer.
# Lives in build_pipeline/; all project paths are derived from $ProjectRoot.

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppName = "arrow"
$DistDir = Join-Path $ProjectRoot "dist\arrow"
$WxsFile = Join-Path $PSScriptRoot "arrow.wxs"
# Installer artifacts (arrow.msi + arrow.wixpdb) stay inside build_pipeline/
$MsiFile = Join-Path $PSScriptRoot "arrow.msi"
$SpecFile = Join-Path $PSScriptRoot "LoOperApp.spec"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$AppExe = Join-Path $DistDir "arrow.exe"
$ReqFile = Join-Path $PSScriptRoot "requirements.txt"

Write-Host "--- arrow MSI Builder ---" -ForegroundColor Cyan

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Green
    Push-Location $ProjectRoot
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
    # Remove the stale looperv2 editable install (previously pulled from the
    # OLD remote repo by requirements.txt).  Its meta-path finder maps `LoOper`
    # to an old clone and can shadow the local working tree during PyInstaller
    # analysis -- the build must ship THIS tree, not the online repo.
    & $PythonExe -m pip uninstall -y looperv2 2>$null | Out-Null
    & $PythonExe -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install Python dependencies from $ReqFile"
        exit 1
    }
}

# Remove PyQt5's bundled (stale) VC++ runtime DLLs from PyQt5/Qt5/bin.
# The PyQt5-Qt5 wheel ships its own msvcp140/vcruntime140 copies, built for
# an older toolset than torch 2.9.1 requires.  PyInstaller 6.x imports every
# collected package (Qt bindings first) in one isolated analysis subprocess;
# once PyQt5 loads, torch/onnxruntime later bind to that stale CRT in-process
# and die with STATUS_ACCESS_VIOLATION ("Windows fatal exception: access
# violation" in torch._load_dll_libraries).  Removing the bundled copies makes
# Qt resolve the runtime from System32 (newest), which the spec already
# bundles for the dist.  Self-healing: reapplies after venv recreation or a
# PyQt5(-Qt5) reinstall.
$PyQt5CrtDir = Join-Path (Join-Path (Split-Path $VenvPython -Parent) "Lib\site-packages") "PyQt5\Qt5\bin"
if (Test-Path $PyQt5CrtDir) {
    foreach ($dll in @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
        $crtPath = Join-Path $PyQt5CrtDir $dll
        if (Test-Path $crtPath) {
            Remove-Item $crtPath -Force
            Write-Host "  Removed stale PyQt5 CRT: $dll" -ForegroundColor Gray
        }
    }
}

# 2.4. Stage llama.cpp binaries for PyInstaller bundling into _internal/bin/
#      The spec (LoOperApp.spec) bundles from LoOper/AI/bin/ during the build.
#      Sources (priority): (1) fork rebuilt with Vulkan (build-vulkan),
#      (2) llama.cpp source build (CPU-only), (3) WinGet system install.
Write-Host "Staging llama.cpp binaries for PyInstaller bundling..." -ForegroundColor Green
$LlamaAiBinDir = Join-Path $ProjectRoot "LoOper\AI\bin"
$WinGetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"

function Get-LlamaBinSourceDir {
    param([string]$ProjectRoot)
    # Priority 1+2: local fork builds (Vulkan-capable build-vulkan first)
    $candidates = @(
        (Join-Path $ProjectRoot "utils\llama.cpp\build-vulkan\bin\Release"),
        (Join-Path $ProjectRoot "utils\llama.cpp\build\bin\Release")
    )
    foreach ($dir in $candidates) {
        if (Test-Path (Join-Path $dir "llama-server.exe")) {
            return $dir
        }
    }
    # Priority 3: WinGet official release (Vulkan + CPU backends)
    if (Test-Path $WinGetRoot) {
        $pkgDirs = Get-ChildItem $WinGetRoot -Directory -Filter "ggml.llamacpp*" -ErrorAction SilentlyContinue
        foreach ($pkgDir in $pkgDirs) {
            if (Test-Path (Join-Path $pkgDir.FullName "llama-server.exe")) {
                return $pkgDir.FullName
            }
        }
    }
    return $null
}

function Sync-LlamaBinaries {
    param([string]$SourceDir, [string]$DestDir)
    if (-not (Test-Path $DestDir)) {
        New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    }
    # Remove stale llama/ggml/mtmd files so a previous (CPU-only) build's
    # DLLs never mix with the new set (PyInstaller bundles the whole dir).
    Get-ChildItem $DestDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(llama|ggml|mtmd)' } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    # Remove a stale cloudflared.exe (Agent Mode no longer bundles a tunnel).
    $StaleCloudflared = Join-Path $DestDir "cloudflared.exe"
    if (Test-Path $StaleCloudflared) {
        Remove-Item $StaleCloudflared -Force -ErrorAction SilentlyContinue
    }
    # Copy the FULL set (exes + DLLs, incl. ggml-vulkan.dll).
    foreach ($f in Get-ChildItem $SourceDir -File -ErrorAction SilentlyContinue) {
        if ($f.Extension -in @('.exe', '.dll')) {
            Copy-Item $f.FullName (Join-Path $DestDir $f.Name) -Force
        }
    }
}

Write-Host "Preparing host-side llama.cpp engine build (engine is not bundled)..." -ForegroundColor Green
# The engine (llama*/ggml*/mtmd*) is EXCLUDED from the bundle by LoOperApp.spec
# and BUILT ON THE HOST at install time (see 2.6).  Nothing is baked here, so a
# build made on this machine can never conflict with an end user's CPU, and the
# developer's own LoOper\AI\bin engine is left untouched for source mode.

# Stage the LOCAL Chrome (the same binary the source app drives) + its
# matching chromedriver for PyInstaller bundling.  player/web/session.py
# launches THIS chrome in frozen builds, so the compiled app runs the exact
# same browser version as the source app on this machine - no downloads, no
# version drift.  Copies the install stub (chrome.exe / chrome_proxy.exe)
# plus the newest versioned folder (chrome.dll + resources) into
# LoOper/AI/bin/chrome/ and the chromedriver uc already cached for that
# version next to it.  The spec bundles the dir to _internal/bin/chrome/.
# Re-runs skip the copy while the staged version is unchanged (marker file).
Write-Host "Staging local Chrome for web automation..." -ForegroundColor Green
$ChromeStageDir = Join-Path $LlamaAiBinDir "chrome"

function Get-ChromeInstallRoot {
    # Where the web engine finds Chrome in source mode (uc.find_chrome_executable
    # resolves the same places: app paths first, then common install roots).
    $roots = @()
    try {
        $appPaths = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue
        if ($appPaths -and $appPaths.'(default)') { $roots += (Split-Path $appPaths.'(default)') }
    } catch {}
    try {
        $appPaths = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue
        if ($appPaths -and $appPaths.'(default)') { $roots += (Split-Path $appPaths.'(default)') }
    } catch {}
    $roots += @(
        (Join-Path ${env:ProgramFiles} "Google\Chrome\Application"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application")
    )
    foreach ($root in ($roots | Select-Object -Unique)) {
        if (Test-Path (Join-Path $root "chrome.exe")) { return $root }
    }
    return $null
}

function Get-NewestChromeVersionDir([string]$InstallRoot) {
    # The versioned folder (chrome.dll + resources) Chrome actually runs from.
    $dirs = Get-ChildItem $InstallRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' }
    if (-not $dirs) { return $null }
    $sorted = $dirs | Sort-Object -Property @{
        Expression = { [version]$_.Name }
        Descending = $true
    }
    return $sorted[0].FullName
}

function Get-UcDriverPath([string]$VersionMajor) {
    # The exact chromedriver undetected_chromedriver already downloaded for the
    # installed Chrome; falls back to fetching the same-version driver once.
    $cached = Join-Path $env:APPDATA "undetected_chromedriver\undetected_chromedriver.exe"
    if (Test-Path $cached) {
        try {
            $fv = (Get-Item $cached).VersionInfo.FileVersion
            if ($fv -and $fv.StartsWith("$VersionMajor.")) { return $cached }
        } catch {}
    }
    $cacheDir = Join-Path $PSScriptRoot "chrome-cft"
    $dl = Join-Path $cacheDir "chromedriver-win64\chromedriver.exe"
    if (Test-Path $dl) { return $dl }
    Write-Host "  Downloading chromedriver $VersionMajor (same version as local Chrome)..." -ForegroundColor Gray
    try {
        $ProgressPreference = 'SilentlyContinue'
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
        $ver = (Get-Item (Join-Path (Get-ChromeInstallRoot) "chrome.exe")).VersionInfo.FileVersion
        $zip = Join-Path $cacheDir "chromedriver.zip"
        Invoke-WebRequest -Uri "https://storage.googleapis.com/chrome-for-testing-public/$ver/win64/chromedriver-win64.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $cacheDir -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        if (Test-Path $dl) { return $dl }
    } catch {
        Write-Warning "chromedriver download failed: $_"
    }
    return $null
}

$LocalChromeRoot = Get-ChromeInstallRoot
$LocalChromeVersionDir = if ($LocalChromeRoot) { Get-NewestChromeVersionDir $LocalChromeRoot } else { $null }
if (-not $LocalChromeRoot -or -not $LocalChromeVersionDir) {
    Write-Warning "Chrome not found on this machine - web nodes will use the system Chrome on target machines"
} else {
    $chromeVersion = Split-Path $LocalChromeVersionDir -Leaf
    $MarkerFile = Join-Path $ChromeStageDir ".chrome-version"
    $marker = if (Test-Path $MarkerFile) { (Get-Content $MarkerFile -Raw -ErrorAction SilentlyContinue).Trim() } else { '' }
    if ($marker -eq $chromeVersion -and (Test-Path (Join-Path $ChromeStageDir "chrome.exe"))) {
        Write-Host "  Chrome $chromeVersion already staged (unchanged)" -ForegroundColor Gray
    } else {
        Write-Host "  Staging Chrome $chromeVersion from $LocalChromeRoot ..." -ForegroundColor Gray
        New-Item -ItemType Directory -Path $ChromeStageDir -Force | Out-Null
        Get-ChildItem $ChromeStageDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        foreach ($rootFile in @("chrome.exe", "chrome_proxy.exe")) {
            $rf = Join-Path $LocalChromeRoot $rootFile
            if (Test-Path $rf) { Copy-Item $rf (Join-Path $ChromeStageDir $rootFile) -Force }
        }
        Copy-Item $LocalChromeVersionDir (Join-Path $ChromeStageDir $chromeVersion) -Recurse -Force
        Set-Content -Path $MarkerFile -Value $chromeVersion
    }
    $driver = Get-UcDriverPath -VersionMajor ($chromeVersion.Split('.')[0])
    if ($driver) {
        Copy-Item $driver (Join-Path $ChromeStageDir "chromedriver.exe") -Force
        Write-Host "  Staged chromedriver $((Get-Item (Join-Path $ChromeStageDir 'chromedriver.exe')).VersionInfo.FileVersion)" -ForegroundColor Gray
    } else {
        Write-Warning "No chromedriver matched Chrome $chromeVersion - bundled browser will need a driver download at runtime"
    }
}
$ChromeStageExe = Join-Path $ChromeStageDir "chrome.exe"
if (Test-Path $ChromeStageExe) {
    $chromeSizeMB = [math]::Round(((Get-ChildItem $ChromeStageDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 0)
    Write-Host "  Staged local Chrome ($chromeSizeMB MB) at $ChromeStageDir" -ForegroundColor Gray
}

if (-not (Test-Path $AppExe)) {
    if (-not (Test-Path $SpecFile)) {
        Write-Error "Spec file not found: $SpecFile"
        exit 1
    }
    Write-Host "Building arrow executable (PyInstaller)..." -ForegroundColor Green
    Push-Location $ProjectRoot
    & $PythonExe -m PyInstaller -y $SpecFile --distpath (Join-Path $ProjectRoot "dist") --workpath (Join-Path $ProjectRoot "build")
    $BuildExitCode = $LASTEXITCODE
    Pop-Location
    if ($BuildExitCode -ne 0 -or -not (Test-Path $AppExe)) {
        Write-Error "Failed to build arrow.exe into $DistDir"
        exit 1
    }
}

# Stage web node resources into dist for WiX bundling.  A full PyInstaller
# rebuild puts inject JS + the local Chrome in _internal via the spec; this
# keeps incremental MSI builds (arrow.exe exists, PyInstaller skipped) from
# producing an installer that is missing them.
Write-Host "Staging web node resources into dist..." -ForegroundColor Green
$WebInjectSrc = Join-Path $ProjectRoot "LoOper\player\web\inject"
foreach ($injDest in @(
        (Join-Path $DistDir "_internal\player\web\inject"),
        (Join-Path $DistDir "_internal\LoOper\player\web\inject")
    )) {
    if ((Test-Path (Join-Path $WebInjectSrc "locators.js")) -and -not (Test-Path (Join-Path $injDest "locators.js"))) {
        New-Item -ItemType Directory -Path $injDest -Force | Out-Null
        Copy-Item (Join-Path $WebInjectSrc "*") $injDest -Force
        Write-Host "  Staged web inject JS -> $injDest" -ForegroundColor Gray
    }
}
$ChromeDistDest = Join-Path $DistDir "_internal\bin\chrome"
if ((Test-Path $ChromeStageExe) -and -not (Test-Path (Join-Path $ChromeDistDest "chrome.exe"))) {
    New-Item -ItemType Directory -Path $ChromeDistDest -Force | Out-Null
    Copy-Item (Join-Path $ChromeStageDir "*") $ChromeDistDest -Recurse -Force
    Write-Host "  Staged local Chrome -> $ChromeDistDest" -ForegroundColor Gray
}

# 2.5. Build license validator
& (Join-Path $ProjectRoot "keygen\build_validator.ps1") -PythonExe $PythonExe -DistDir (Join-Path $ProjectRoot "dist")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to build license_validator.exe"
    exit 1
}

# 2.6. Ship the llama.cpp SOURCE + the host-side engine provisioner.  The engine
#      itself is BUILT on the target machine at install time
#      (install_dependencies.ps1 -> setup_llama_engine.ps1); nothing is baked
#      here.  Exclude VCS/build outputs to keep the payload lean.
Write-Host "Staging llama.cpp source for the on-host engine build..." -ForegroundColor Green
# Ship the source as ONE zip, not ~38k loose files.  generate_wxs.py emits one
# MSI Component per file and Windows Installer hard-caps an MSI at 65536
# Components (WIX7502) - the external-cab mechanism only lifts the 2 GB SIZE
# cap, not the component count.  One zip = one Component; install_dependencies
# unpacks it next to the app before the on-host build.
$LlamaSrcTree = Join-Path $ProjectRoot "utils\llama.cpp"
$LlamaSrcZip = Join-Path $DistDir "_internal\llama.cpp.zip"
$LlamaSrcDest = Join-Path $DistDir "_internal\llama.cpp"
if (Test-Path $LlamaSrcTree) {
    if (Test-Path $LlamaSrcDest) { Remove-Item $LlamaSrcDest -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $LlamaSrcZip) { Remove-Item $LlamaSrcZip -Force -ErrorAction SilentlyContinue }
    # bsdtar ships with Windows 10+ and handles ~38k files far faster than
    # Compress-Archive.  Same exclusions the robocopy staging used.
    tar -a -c -f $LlamaSrcZip --exclude=.git --exclude=build --exclude=build-vulkan --exclude=build-local -C $LlamaSrcTree .
    if (Test-Path $LlamaSrcZip) {
        Write-Host "  Staged llama.cpp source -> $LlamaSrcZip ($([math]::Round((Get-Item $LlamaSrcZip).Length / 1MB, 0)) MB)" -ForegroundColor Gray
    } else {
        Write-Warning "llama.cpp source zip was not created - the installer cannot build the engine in place."
    }
} else {
    Write-Warning "llama.cpp source not found at $LlamaSrcTree - the installer cannot build the engine in place."
}
$EngineScriptSrc = Join-Path $PSScriptRoot "setup_llama_engine.ps1"
if (Test-Path $EngineScriptSrc) {
    Copy-Item $EngineScriptSrc $DistDir -Force
    Write-Host "  Staged setup_llama_engine.ps1 -> $DistDir" -ForegroundColor Gray
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
Push-Location $PSScriptRoot
& $PythonExe generate_wxs.py
$GenerateExitCode = $LASTEXITCODE
Pop-Location
if ($GenerateExitCode -ne 0) {
    Write-Error "Failed to generate .wxs file."
    exit 1
}

# 4. Build MSI
if ($WixInstalled) {
    Write-Host "Building MSI..." -ForegroundColor Green
    wix eula accept wix7 *> $null
    wix extension add -g WixToolset.UI.wixext *> $null
    Push-Location $PSScriptRoot
    wix build -acceptEula wix7 -arch x64 $WxsFile -ext WixToolset.UI.wixext -o $MsiFile
    $WixExitCode = $LASTEXITCODE
    Pop-Location
    if ($WixExitCode -eq 0) {
        Write-Host "Successfully created $MsiFile" -ForegroundColor Green
        # EmbedCab="no": payload ships as external cabinets next to the MSI.
        # They must be distributed alongside arrow.msi (single msi >2 GB is a
        # Windows Installer hard limit).
        $CabFiles = Get-ChildItem $PSScriptRoot -File -Filter "cab*.cab" -ErrorAction SilentlyContinue
        if ($CabFiles) {
            $cabMB = [math]::Round((($CabFiles | Measure-Object Length -Sum).Sum) / 1MB, 0)
            Write-Host "  Payload cabinets ($($CabFiles.Count) files, $cabMB MB) - distribute with arrow.msi:" -ForegroundColor Cyan
            $CabFiles | ForEach-Object { Write-Host "    $($_.Name)" -ForegroundColor Gray }
        }
        Write-Host ""
        Write-Host "DISTRIBUTE as a folder: arrow.msi + all *.cab above + the >2 GB" -ForegroundColor Cyan
        Write-Host "LocateAnything-3B-Q8_0.gguf model (Windows Installer and Burn CABs" -ForegroundColor Cyan
        Write-Host "cap a single file at 2 GB, so it ships loose).  The MSI copies it" -ForegroundColor Cyan
        Write-Host "into the app models dir automatically during install." -ForegroundColor Cyan
    } else {
        Write-Error "Failed to build MSI."
    }
} else {
    Write-Host "Please install WiX and run: wix build $WxsFile -o $MsiFile" -ForegroundColor Cyan
}

Write-Host "Done."
