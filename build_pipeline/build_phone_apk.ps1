<#
.SYNOPSIS
  Build the LoOper phone client (cross-platform Pear/Bare) into an Android APK.

.DESCRIPTION
  Orchestrates the full Android build for LoOper/phone_client:
    1. verifies prerequisites (Node, JDK 17, Android SDK + NDK, Gradle)
    2. fetches the bare-kit prebuild into android/app/libs/bare-kit when missing
    3. installs the JS deps (hyperdht, bare-net, b4a, bare-link, bare-pack)
    4. runs gradle assemble<Debug|Release> (its preBuild links addons + packs the
       core/entry.js bundle)

  The cross-platform Bare core (phone_client/core) is untouched by this script —
  only the Android shell is built here.

.PARAMETER Configuration
  Debug (default) or Release.

.PARAMETER BareKitVersion
  holepunchto/bare-kit release tag for the prebuild, e.g. v2.5.5.

.PARAMETER SkipNpmInstall
  Skip `npm install` (deps already present).

.EXAMPLE
  pwsh build_pipeline/build_phone_apk.ps1
  pwsh build_pipeline/build_phone_apk.ps1 -Configuration Release
#>
param(
  [ValidateSet("Debug", "Release")] [string]$Configuration = "Debug",
  [string]$BareKitVersion = "v2.5.5",
  [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Stop"

function Info([string]$m) { Write-Host "[phone] $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "[phone] $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "[phone] $m" -ForegroundColor Red; exit 1 }

# --- paths (repo_root/build_pipeline/<this file>) ---
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$ClientDir  = Join-Path $RepoRoot "LoOper\phone_client"
$AndroidDir = Join-Path $ClientDir "android"
$AppDir     = Join-Path $AndroidDir "app"
$BareKitDir = Join-Path $AppDir "libs\bare-kit"

if (-not (Test-Path (Join-Path $ClientDir "core\entry.js"))) {
  Fail "phone_client core not found at $ClientDir\core\entry.js"
}

# --- prerequisites ---
Info "Checking prerequisites..."
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Fail "Node.js not found. Install from https://nodejs.org/ and retry."
}
# JDK 17 - env, else a vendor dir, else java on PATH.
$java = $null
if ($env:JAVA_HOME) { $java = Join-Path $env:JAVA_HOME "bin\java.exe" }
if (-not $java -or -not (Test-Path $java)) {
  foreach ($b in 'C:\Program Files\Eclipse Adoptium','C:\Program Files\Java','C:\Program Files\Microsoft','C:\Program Files\Zulu') {
    if (Test-Path $b) {
      $hit = Get-ChildItem $b -Directory -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -match '17' } | Select-Object -First 1
      if ($hit) { $env:JAVA_HOME = $hit.FullName; $java = Join-Path $hit.FullName "bin\java.exe"; break }
    }
  }
}
if (-not $java -or -not (Test-Path $java)) {
  $javaCmd = Get-Command java -ErrorAction SilentlyContinue
  if ($javaCmd) { $java = $javaCmd.Source }
}
if (-not $java) { Fail "JDK 17 not found. Run Arrow_setup.bat (step 0c) or set JAVA_HOME." }

# Android SDK - env, else the default per-user location Android Studio creates.
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME }
       elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT }
       elseif (Test-Path "$env:LOCALAPPDATA\Android\Sdk") { "$env:LOCALAPPDATA\Android\Sdk" }
       else { $null }
if (-not $sdk) { Fail "Android SDK not found. Run Arrow_setup.bat (step 0c) or set ANDROID_HOME." }
# Expose it to the Gradle child processes (bare-link + AGP read these).
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
Info "Android SDK: $sdk"
# bare-link cross-compiles the native addons, so it needs the NDK on the env.
$ndk = Join-Path $sdk "ndk\27.2.12479018"
if (Test-Path $ndk) { $env:ANDROID_NDK_HOME = $ndk; $env:ANDROID_NDK_ROOT = $ndk }
if (-not (Test-Path (Join-Path $sdk "platforms\android-35"))) {
  Warn "platform 35 missing -> run Arrow_setup.bat, or: sdkmanager 'platforms;android-35'"
}
if (-not (Test-Path (Join-Path $sdk "ndk\27.2.12479018"))) {
  Warn "NDK 27.2.12479018 missing -> run Arrow_setup.bat, or: sdkmanager 'ndk;27.2.12479018'"
}

# --- bare-kit prebuild ---
if (-not (Test-Path (Join-Path $BareKitDir "classes.jar"))) {
  Info "bare-kit prebuild missing -> downloading $BareKitVersion ..."
  if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail ("GitHub CLI (gh) is required to fetch the bare-kit prebuild. Install gh," +
          " or unpack android/bare-kit from the release into:`n  $BareKitDir")
  }
  $tmp = Join-Path $env:TEMP ("barekit_" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $tmp -Force | Out-Null
  Push-Location $tmp
  try {
    & gh release download --repo holepunchto/bare-kit $BareKitVersion -p "prebuilds.zip"
    if ($LASTEXITCODE -ne 0) { Fail "gh release download failed for $BareKitVersion" }
    Expand-Archive -Path "prebuilds.zip" -DestinationPath "." -Force
    # prebuilds.zip => android/bare-kit
    $src = Get-ChildItem -Recurse -Directory -Filter "bare-kit" |
           Where-Object { $_.Parent -and $_.Parent.Name -eq "android" } |
           Select-Object -First 1
    if (-not $src) { $src = Get-ChildItem -Recurse -Directory -Filter "bare-kit" | Select-Object -First 1 }
    if (-not $src) { Fail "bare-kit directory not found inside prebuilds.zip" }
    New-Item -ItemType Directory -Path (Split-Path $BareKitDir -Parent) -Force | Out-Null
    Copy-Item -Recurse -Force $src.FullName $BareKitDir
    Info "bare-kit installed -> $BareKitDir"
  }
  finally {
    Pop-Location
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  }
} else {
  Info "bare-kit prebuild present."
}

# --- JS dependencies ---
if (-not $SkipNpmInstall) {
  Info "Installing JS dependencies (hyperdht, bare-net, b4a, bare-link, bare-pack)..."
  Push-Location $ClientDir
  try {
    & npm install
    if ($LASTEXITCODE -ne 0) { Fail "npm install failed in $ClientDir" }
  }
  finally { Pop-Location }
}

# --- gradle build (preBuild links addons + packs core/entry.js) ---
$task = if ($Configuration -eq "Release") { "assembleRelease" } else { "assembleDebug" }
$gradlew = Join-Path $AndroidDir "gradlew.bat"
$portableGradle = Join-Path $RepoRoot ".tools\gradle\bin\gradle.bat"  # Arrow_setup.bat step 0c

Push-Location $AndroidDir
try {
  if (Test-Path $gradlew) {
    Info "Running gradlew $task ..."
    & $gradlew $task
  } elseif (Test-Path $portableGradle) {
    Info "Running portable Gradle $task ..."
    & $portableGradle $task
  } elseif (Get-Command gradle -ErrorAction SilentlyContinue) {
    Info "Running gradle $task ..."
    & gradle $task
  } else {
    Fail ("No Gradle found. Run Arrow_setup.bat (step 0c) to install it, or run" +
          " 'gradle wrapper' in $AndroidDir with any Gradle.")
  }
  if ($LASTEXITCODE -ne 0) { Fail "Gradle build failed." }
}
finally { Pop-Location }

# --- result ---
$apkDir = Join-Path $AppDir "build\outputs\apk"
$apk = Get-ChildItem -Path $apkDir -Recurse -Filter "*.apk" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($apk) {
  Info "Done. APK: $($apk.FullName)"
} else {
  Warn "Build finished but no APK found under $apkDir"
}
