<#
setup_llama_engine.ps1 — host-side llama.cpp engine provisioner.

Runs ON THE TARGET MACHINE (install wizard / first run) — never at build time.
It DETECTS the host, ensures the C++ toolchain (CMake + VS Build Tools) when a
local build is required, compiles the vendored llama.cpp fork FOR THIS HOST,
and stages the engine beside the app.  Nothing ISA-specific is baked at build
time, so a binary built on a developer machine can never "conflict" with the
end user's CPU.

The CPU backend is built with GGML_CPU_ALL_VARIANTS (runtime ISA dispatch), so
the produced engine runs on ANY x64 CPU down to the SSE4.2 baseline while still
using AVX2/AVX512 when the host supports them (llama.cpp DEFAULTS GGML_AVX2=ON,
which on MSVC bakes /arch:AVX2 -> STATUS_ILLEGAL_INSTRUCTION 0xC000001D on CPUs
without AVX2; ALL_VARIANTS avoids that).

Falls back to the official ggml-org prebuilt (portable, all variants) when a
local build cannot happen.  Idempotent: a working llama-server.exe is verified
(llama-server --version) and left alone unless -Force is given.
#>
param(
    [Parameter(Mandatory = $true)][string]$BinDest,   # e.g. <app>\_internal\bin
    [string]$SourceDir = "",                           # llama.cpp fork source
    [switch]$Force                                     # rebuild even if present
)

$ErrorActionPreference = "Continue"

function Log([string]$m)  { Write-Host "[engine] $m" }
function Warn([string]$m) { Write-Warning "[engine] $m" }

function Refresh-Path {
    $env:PATH = "$env:PATH;$env:LOCALAPPDATA\Microsoft\WinGet\Links;C:\Program Files\CMake\bin;$env:LOCALAPPDATA\Programs\CMake\bin"
}

# Verify an engine actually RUNS on this host (catches the 0xC000001D class).
function Test-EngineAt([string]$dir) {
    if (-not $dir) { return $false }
    $exe = Join-Path $dir "llama-server.exe"
    if (-not (Test-Path $exe)) { return $false }
    try {
        $out = "$env:TEMP\arrow_eng_out.txt"
        $p = Start-Process -FilePath $exe -ArgumentList "--version" -NoNewWindow -PassThru `
                -RedirectStandardOutput $out -RedirectStandardError "$env:TEMP\arrow_eng_err.txt"
        if (-not $p.WaitForExit(15000)) { try { $p.Kill() } catch {}; return $false }
        return ($p.ExitCode -eq 0)
    } catch { return $false }
}

function Ensure-Toolchain {
    Refresh-Path
    $cmakeOk = [bool](Get-Command cmake -ErrorAction SilentlyContinue)
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $vcOk = $false
    if (Test-Path $vswhere) {
        $p = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($p) { $vcOk = $true }
    }
    if ($cmakeOk -and $vcOk) { Log "C++ toolchain present (CMake + MSVC)"; return $true }

    Log "Installing the C++ toolchain (CMake + VS Build Tools) - this may prompt for admin..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (-not $cmakeOk) {
            winget install --id Kitware.CMake -e --silent --accept-package-agreements --accept-source-agreements
        }
        if (-not $vcOk) {
            winget install --id Microsoft.VisualStudio.2022.BuildTools -e --silent --accept-package-agreements --accept-source-agreements `
                --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        }
    } else {
        if (-not $cmakeOk) {
            $zip = "$env:TEMP\cmake-portable.zip"
            try {
                Invoke-WebRequest "https://github.com/Kitware/CMake/releases/download/v3.30.5/cmake-3.30.5-windows-x86_64.zip" -OutFile $zip -UseBasicParsing
                Expand-Archive $zip -DestinationPath "$env:LOCALAPPDATA\Programs\CMake" -Force
            } catch { Warn "CMake download failed: $_" }
        }
        if (-not $vcOk) {
            $vs = "$env:TEMP\vs_BuildTools.exe"
            try {
                Invoke-WebRequest "https://aka.ms/vs/17/release/vs_BuildTools.exe" -OutFile $vs -UseBasicParsing
                Start-Process $vs -ArgumentList "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" -Wait
            } catch { Warn "VS Build Tools install failed: $_" }
        }
    }
    Refresh-Path
    return [bool](Get-Command cmake -ErrorAction SilentlyContinue)
}

function Stage-Engine([string]$srcDir, [string]$dest) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem $srcDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".exe", ".dll" } |
        ForEach-Object { Copy-Item $_.FullName $dest -Force }
}

function Build-FromSource([string]$src, [string]$dest) {
    if (-not (Test-Path (Join-Path $src "CMakeLists.txt"))) { Warn "no llama.cpp source at $src"; return $false }
    if (-not (Ensure-Toolchain)) { Warn "C++ toolchain unavailable - cannot build locally"; return $false }
    $build = Join-Path $src "build-local"
    $backend = ""
    if ($env:VULKAN_SDK) { Log "Vulkan SDK detected - enabling the Vulkan backend"; $backend = "-DGGML_VULKAN=ON" }
    $arch = ""
    if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { $arch = "-A arm64" }

    Log "Configuring llama.cpp for this host..."
    & cmake -S $src -B $build -DCMAKE_BUILD_TYPE=Release $backend `
        "-DGGML_NATIVE=OFF" "-DGGML_CPU_ALL_VARIANTS=ON" $arch `
        -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
    if ($LASTEXITCODE -ne 0) { Warn "cmake configure failed"; return $false }

    Log "Building the engine (this can take several minutes)..."
    & cmake --build $build --config Release --parallel
    foreach ($cand in @((Join-Path $build "bin\Release"), (Join-Path $build "bin"))) {
        if (Test-Path (Join-Path $cand "llama-server.exe")) {
            Stage-Engine $cand $dest
            if (Test-EngineAt $dest) { Log "engine built from source and verified"; return $true }
            Warn "built engine did not run - will fall back to a prebuilt"
            return $false
        }
    }
    Warn "build produced no llama-server.exe"
    return $false
}

function Get-Prebuilt([string]$dest) {
    Log "Fetching the official ggml-org prebuilt (portable, all ISA variants)..."
    $zip = "$env:TEMP\arrow_llama_prebuilt.zip"
    try {
        $rels = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=40" -Headers @{ "User-Agent" = "arrow-setup" }
        $variants = @("vulkan-x64", "cpu-x64")
        if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { $variants = @("cpu-arm64") }
        $url = $null
        foreach ($v in $variants) {
            foreach ($r in $rels) {
                $a = $r.assets | Where-Object { $_.name -like ("*-bin-win-" + $v + ".zip") } | Select-Object -First 1
                if ($a) { $url = $a.browser_download_url; break }
            }
            if ($url) { break }
        }
        if (-not $url) { Warn "no prebuilt asset found"; return $false }
        Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    } catch { Warn "prebuilt download failed: $_"; return $false }

    $d = "$env:TEMP\arrow_llama_prebuilt"
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
    Expand-Archive $zip -DestinationPath $d -Force
    $srv = Get-ChildItem $d -Recurse -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $srv) { Warn "prebuilt extraction failed"; return $false }
    Stage-Engine $srv.DirectoryName $dest
    if (Test-EngineAt $dest) { Log "prebuilt engine staged and verified"; return $true }
    Warn "prebuilt engine did not run"
    return $false
}

# ---------------------------------- main ----------------------------------
New-Item -ItemType Directory -Force -Path $BinDest | Out-Null

if (-not $Force -and (Test-EngineAt $BinDest)) {
    Log "engine already present and runs on this host - nothing to do"
    exit 0
}

$ok = $false
if ($SourceDir -and (Test-Path $SourceDir)) {
    $ok = Build-FromSource $SourceDir $BinDest
} else {
    Warn "no llama.cpp source shipped - cannot build in place"
}
if (-not $ok) { $ok = Get-Prebuilt $BinDest }

if ($ok -and (Test-EngineAt $BinDest)) {
    Log "engine ready at $BinDest"
    exit 0
}
Warn "engine provisioning incomplete - LLM/vision nodes will be unavailable"
exit 1
