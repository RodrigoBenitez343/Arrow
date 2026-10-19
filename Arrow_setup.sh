#!/usr/bin/env bash
# ============================================================================
#  arrow / LoOper - one-shot bootstrap + launcher (Linux equivalent of
#  Arrow_setup.bat)
# ----------------------------------------------------------------------------
#  Clone the repo and run this file on a freshly-imaged Linux box.  It mirrors
#  the Windows .bat step for step, with GRACEFUL DEGRADATION for anything that
#  is Windows-specific:
#
#    0.   PULL the latest source from the remote repo (skipped when git missing)
#    0b.  CHECK prerequisites (python3, cc/gcc for native wheels, cmake, curl,
#         unzip).  Missing tools are reported with apt hints, never fatal.
#    0c.  Phone-client build prerequisites (node, JDK 17, Android SDK cmdline
#         tools, Gradle).  Set SKIP_PHONE=1 to skip.
#    1.   Create the venv and install deps (pip install -e . + pinned lock).
#         >>> Windows-only packages are detected, skipped, and DUMPED to
#             windows_packages_report.md for manual analysis. <<<
#    1b.  Apply vendored library patches (LoOper/patches/apply_patches.py).
#    2.   Provide the llama.cpp engine: reuse an existing build, else build
#         utils/llama.cpp (CPU or Vulkan), else download a prebuilt LINUX
#         release - so no compiler is ever required.
#    3.   DOWNLOAD the base models + browser the app needs (same pinned
#         revisions as the .bat; Chrome/chromedriver/llama.cpp use the linux64
#         assets; the Windows-only laya Vulkan .exe is reported and skipped).
#    4.   RDP sandbox: WINDOWS-ONLY (RDPWrap / net user / reg / cmdkey) -
#         on Linux this is reported as skipped.
#    5.   Ask Debug vs Normal, then launch LoOper/main.py
#
#  No root is required.  apt install hints are printed, never executed.
# ============================================================================

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"
PYTHON=""
AI_BIN="$ROOT/LoOper/AI/bin"
AI_MODELS="$ROOT/LoOper/AI/models"
LAY_DIR="$AI_MODELS/laya-GGUF"
LFM_DIR="$AI_MODELS/LFM2.5-VL-450M-GGUF"
DATA_DIR="$ROOT/LoOper/data"
VOSK_DIR="$DATA_DIR/vosk_models"
PIPER_DIR="$DATA_DIR/piper_voices"

# Windows-specific packages collected during the run -> this markdown report
WIN_REPORT="$ROOT/windows_packages_report.md"
WIN_TMP="$(mktemp -d /tmp/arrow_win_pkgs.XXXXXX)"
WIN_SKIPPED="$WIN_TMP/skipped.tsv"
WIN_ISOLATED="$WIN_TMP/isolated.tsv"
TMPDIR_D="$(mktemp -d /tmp/arrow_setup_tmp.XXXXXX)"
export TMPDIR_D
: > "$WIN_SKIPPED"
: > "$WIN_ISOLATED"
export WIN_SKIPPED

trap 'rm -rf "$WIN_TMP" "$TMPDIR_D"' EXIT

log()  { echo "[setup] $*"; }
warn() { echo "[warn ] $*" >&2; }

# ============================================================================
#  0. Pull the latest source from the remote repo
# ============================================================================
if ! command -v git >/dev/null 2>&1; then
  warn "[git] git not found on PATH - skipping update."
elif git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "[git] Pulling latest from remote..."
  git -C "$ROOT" pull || warn "[git] git pull FAILED - continuing with existing source."
else
  warn "[git] $ROOT is not a git repository - skipping update."
fi

# ============================================================================
#  Helper: the Windows-package probe (pure stdlib python, runs pre-install)
#  Parses pyproject.toml dependencies + build_pipeline/requirements.txt,
#  classifies each package as windows-only (marker or name heuristic) or
#  linux-compatible, and writes:
#    $1 = cleaned requirements file (linux-safe specs, one per line)
#    $2 = skipped TSV  (spec \t category \t reason \t source)
# ============================================================================
write_probe() {
  cat > "$WIN_TMP/win_probe.py" <<'PYEOF'
"""Classify Arrow's pinned deps for Linux and dump the Windows-only set."""
import os, re, sys
from pathlib import Path

root = Path(sys.argv[1]); out_req = Path(sys.argv[2])
skipped_tsv = Path(os.environ["WIN_SKIPPED"])
entries = []  # (spec, marker, source, is_url_or_path)

def add(spec, source):
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return
    if spec.startswith("-"):            # pip options (-e ., --platform, ...)
        if re.search(r"--platform[= ](win32|windows)", spec, re.I):
            skipped_tsv.open("a").write(f"{spec}\tPip option\t--platform win target\t{source}\n")
        return
    is_local = spec.startswith((".", "/", "file:", "-e"))
    full = spec
    if ";" in spec:                       # env marker is everything after ';'
        base, _, marker = spec.partition(";")
        spec, marker = base.strip(), marker.strip()
    else:
        marker = ""
    entries.append((spec, marker, full, source, is_local))

def parse_pyproject(path):
    txt = path.read_text(encoding="utf-8", errors="replace")
    for key in ("dependencies", "optional-dependencies", "build-system"):
        for block in re.findall(key + r"\s*=\s*\[(.*?)\]", txt, re.S):
            # allow nested single quotes inside double-quoted PEP-508 strings
            for item in re.findall(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', block):
                s = (item[0] or item[1]).strip()
                if s:
                    add(s, "pyproject.toml")

def parse_requirements(path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        add(line, "requirements.txt")

def name_of(spec):
    return re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0].strip().lower().replace("_", "-")

# Windows-only markers - the manifest is honest about these.
WIN_MARK = re.compile(
    r"(sys_platform\s*==\s*['\"]win32['\"]|platform_system\s*==\s*['\"]Windows['\"]"
    r"|os_name\s*==\s*['\"]nt['\"]|python_version\s*!=\s*['\"]3\.13['\"]\s*and\s*sys_platform)", re.I)
def is_win_marker(marker):
    if not marker:
        return False, ""
    if WIN_MARK.search(marker):
        return True, f"env marker: {marker}"
    # generic marker evaluation: any residual marker mentioning win32/Windows/nt
    if re.search(r"['\"](win32|windows|nt)['\"]", marker, re.I):
        return True, f"env marker references Windows: {marker}"
    return False, ""

# Name heuristics: wheels that simply do not exist on Linux.
WIN_NAME = re.compile(
    r"^(pywin32|pypiwin32|pywinpty|win32[._-].*|python[._-]win[._-]?registry"
    r"|pycaw|uiautomation|pygetwindow|comtypes|wmi|ntsecuritycon"
    r"|windows[._-].*)$"
    r"|\.exe$|\.dll$|\.win32|_win32|win_amd64|win-i386", re.I)
# Windows-first: installs fine on Linux, but its runtime API is Win32-only.
WIN_RUNTIME = {"keyboard", "mouse"}
SAFE_ON_LINUX = {  # cross-platform despite the name - do NOT flag
    "pywin32-ctypes", "pyreadline3", "altgraph",
    "mouseinfo", "pyrect", "pyautogui", "pyscreeze", "pyperclip",
}

for f, fn in ((root / "pyproject.toml", parse_pyproject),
              (root / "build_pipeline" / "requirements.txt", parse_requirements)):
    if f.exists():
        fn(f)

keep, win, info = [], [], []
seen_keep = set()
# Marker inheritance: build_pipeline/requirements.txt is the same set stripped
# of PEP-508 markers, so a name gated to win32 in pyproject is Windows-only
# even where the lockfile lists it bare.
win_names = set()
for spec, marker, full, source, is_local in entries:
    if not is_local and is_win_marker(marker)[0]:
        win_names.add(name_of(spec))
for spec, marker, full, source, is_local in entries:
    n = name_of(spec) if not is_local else spec
    if is_local:
        if full not in seen_keep:
            seen_keep.add(full); keep.append(full)
        continue
    w, why = is_win_marker(marker)
    cat = "Marker-gated (win32/Windows)" if w else ""
    if not w and n in win_names:
        w, cat = True, "Marker-gated (win32/Windows)"
        why = f"same package is win32-gated in the other manifest"
    if not w and (WIN_NAME.search(n) or WIN_NAME.search(spec)):
        w, cat = True, "Name heuristic (no Linux wheel / Windows API)"
        why = why or "package name/known wheel set is Windows-targeted"
    if n in SAFE_ON_LINUX:
        w = False
    if w:
        win.append((spec, n, cat or "Windows-only", why, source))
    else:
        if n in WIN_RUNTIME:
            info.append((full, "Windows-first runtime (needs root/Win32 API on the target; works via X11/uinput only with extra setup)", source))
        if full not in seen_keep:
            seen_keep.add(full); keep.append(full)

out_req.write_text("\n".join(keep) + "\n", encoding="utf-8")
with skipped_tsv.open("a", encoding="utf-8") as fh:
    for spec, n, cat, why, source in win:
        fh.write(f"{spec}\t{cat}\t{why}\t{source}\n")
info_tsv = Path(str(skipped_tsv) + ".info")
with info_tsv.open("w", encoding="utf-8") as fh:
    for full, why, source in info:
        fh.write(f"{full}\t{why}\t{source}\n")

print(f"[probe] {len(entries)} manifest entries -> {len(keep)} linux-safe, "
      f"{len(win)} windows-only flagged, {len(info)} windows-first runtime")
for spec, n, cat, why, source in win:
    print(f"   [win ] {spec}  ({cat}; {source})")
PYEOF
}

# ============================================================================
#  Report writer: dump collected Windows-specific packages as markdown
# ============================================================================
write_report() {
  local probe_py="$1"
  {
    echo "# Windows-specific packages detected by Arrow_setup.sh"
    echo
    echo "- Generated: $(date -Is)  ·  host: $(uname -srm)  ·  python: ${PYTHON:-not found}"
    echo "- Source manifests parsed: \`pyproject.toml\` + \`build_pipeline/requirements.txt\`"
    echo "- The Linux install proceeded WITHOUT these (graceful degradation)."
    echo
    echo "## 1. Detected from the manifests (Windows-only or Windows-heuristic)"
    echo
    echo "| Package spec | Category | Reason | Source |"
    echo "|---|---|---|---|"
    awk -F'\t' '$2!="Pip option" && $2!="" {printf "| %s | %s | %s | %s |\n", $1, $2, $3, $4}' "$WIN_SKIPPED" | sort -u
    echo
    echo "## 2. Windows-first packages that DID install on Linux (kept, for review)"
    echo
    echo "_These have cross-platform wheels, so the install keeps them - but their runtime API is Win32-oriented and their behaviour on Linux needs verification._"
    echo
    echo "| Package spec | Note | Source |"
    echo "|---|---|---|"
    if [ -s "$WIN_SKIPPED.info" ]; then
      awk -F'\t' '{printf "| %s | %s | %s |\n", $1, $2, $3}' "$WIN_SKIPPED.info" | sort -u
    else
      echo "| _none_ | | |"
    fi
    echo
    echo "## 3. Failed to install in isolation on this Linux host"
    echo
    echo "_These are NOT manifest-flagged; pip could not build/install them here. Wheel/platform gaps to analyse._"
    echo
    echo "| Package | Failure (first line) |"
    echo "|---|---|"
    if [ -s "$WIN_ISOLATED" ]; then
      awk -F'\t' '{printf "| %s | %s |\n", $1, substr($2,1,300)}' "$WIN_ISOLATED"
    else
      echo "| _none_ | all non-flagged packages installed cleanly |"
    fi
    echo
    echo "## 4. Known Windows-side artifacts skipped by this script"
    echo
    echo "- \`laya.exe\` (ggmlc Vulkan release) - no Linux binary published; decision-engine nodes need a Linux port or Ollama fallback."
    echo "- \`LoOper/AI/bin/*.dll\` staged by the .bat llama build - Linux equivalents staged instead (\`lib*.so\`)."
    echo "- RDP sandbox (RDPWrap, \`net user\`, \`reg\`, \`cmdkey\`, \`netsh\`) - Windows Remote Desktop stack; skipped entirely."
    echo "- \`chrome-win64\`/\`chromedriver.exe\` - staged as \`chrome-linux64\`/\`chromedriver\` instead."
    echo
    echo "## 5. Linux replacements to consider while analyzing"
    echo
    echo "- \`pywin32\`/\`pypiwin32\`/\`comtypes\`/\`PyGetWindow\` → \`python-xlib\` + \`wmctrl\`/\`xdotool\` (window management)."
    echo "- \`keyboard\`/\`mouse\` → uinput via \`python-evdev\`, or \`xdotool\`/\`ydotool\` (needs input-group membership)."
    echo "- \`MouseInfo\`/\`pyautogui\` screen bits → \`mss\`/\`grim\` already in the tree work on Linux."
    echo "- \`win32_setctime\`, \`pyreadline3\`, \`pywin32-ctypes\` → no-op on Linux (already installed where cross-platform)."
  } > "$WIN_REPORT"
  log "Windows-package report written: $WIN_REPORT"
}

# ============================================================================
#  0b. Prerequisites (per-user, no sudo executed)
# ============================================================================
find_python() {
  PYTHON=""
  local c
  for c in python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PYTHON="$c"; return; fi
  done
}
find_python
if [ -z "$PYTHON" ]; then
  echo "[error] Python 3 not found. Install it, e.g.:" >&2
  echo "        sudo apt install python3 python3-venv python3-pip" >&2
  exit 1
fi
log "[prereq] Using Python: $PYTHON ($($PYTHON --version 2>&1))"

missing=()
command -v curl  >/dev/null 2>&1 || missing+=("curl")
command -v unzip >/dev/null 2>&1 || missing+=("unzip")
command -v git   >/dev/null 2>&1 || missing+=("git")
command -v cmake >/dev/null 2>&1 || missing+=("cmake")
command -v cc    >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || missing+=("build-essential (cc/gcc - needed for numpy and other source builds)")
if [ ${#missing[@]} -gt 0 ]; then
  warn "[prereq] Missing: ${missing[*]}"
  warn "         Suggested: sudo apt install curl unzip git cmake build-essential python3-venv python3-dev"
  warn "         Continuing anyway - each dependent step degrades gracefully."
fi

# ============================================================================
#  Download helpers (defined early - used by steps 0c and 3)
# ============================================================================
# ---- getfile <url> <dest>  resumable, skipped when present -----------------
getfile() {
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then echo "  [skip] $(basename "$dest")"; return 0; fi
  echo "  [get ] $dest"
  mkdir -p "$(dirname "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 5 --retry-delay 2 --connect-timeout 30 -C - -o "$dest.part" "$url" || {
      warn "  [warn] download interrupted: $url"
      warn "         partial kept at $dest.part - re-run this script to resume."
      return 0
    }
  elif command -v wget >/dev/null 2>&1; then
    wget -c -O "$dest.part" "$url" || {
      warn "  [warn] download interrupted: $url"
      return 0
    }
  else
    warn "  [warn] neither curl nor wget available - cannot download $url"
    return 0
  fi
  mv -f "$dest.part" "$dest"
}

untar() {  # extract .tar with python (same trick as the .bat)
  mkdir -p "$2"
  "$VPY" -c "import sys,tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2])" "$1" "$2"
}

# ============================================================================
#  0c. Phone-client build prerequisites (Linux equivalents)
# ============================================================================
ensure_phone_prereqs() {
  if [ "${SKIP_PHONE:-}" = "1" ]; then
    log "[phone] SKIP_PHONE=1 - skipping phone-client prerequisites."
    return 0
  fi
  ANDROID_SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
  CMDLINE_TOOLS="$ANDROID_SDK/cmdline-tools/latest/bin"
  NDK_VER=27.2.12479018
  PLATFORM_VER=android-35

  command -v node >/dev/null 2>&1 \
    && log "[phone] Node.js present." \
    || warn "[phone] Node.js not found - install via your distro (nodesource/apt) if you build the phone client."

  if command -v node >/dev/null 2>&1 && [ ! -d "$ROOT/LoOper/agent_sidecar/node_modules/hyperdht" ]; then
    log "[phone] Installing Agent Mode sidecar deps (hyperdht, b4a)..."
    (cd "$ROOT/LoOper/agent_sidecar" && npm install) || warn "[phone] npm install failed - Agent Mode remote access unavailable."
  fi

  if command -v java >/dev/null 2>&1 && java -version 2>&1 | head -1 | grep -q '"17'; then
    log "[phone] JDK 17 present."
  else
    warn "[phone] JDK 17 not found - e.g. sudo apt install temurin-17-jdk (or openjdk-17-jdk)."
  fi

  if ! command -v gh >/dev/null 2>&1; then
    warn "[phone] GitHub CLI not found - needed for the bare-kit prebuild during APK builds."
  fi

  # Android command-line tools (linux variant of the .bat download)
  if [ ! -f "$CMDLINE_TOOLS/sdkmanager" ]; then
    log "[phone] Android command-line tools missing - downloading linux version..."
    getfile "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip" "$TMPDIR_D/cmdline-tools.zip"
    if [ -f "$TMPDIR_D/cmdline-tools.zip" ]; then
      mkdir -p "$ANDROID_SDK/cmdline-tools"
      rm -rf "$TMPDIR_D/cmdline_tools" "$ANDROID_SDK/cmdline-tools/latest"
      unzip -q "$TMPDIR_D/cmdline-tools.zip" -d "$TMPDIR_D/cmdline_tools"
      mv "$TMPDIR_D/cmdline_tools/cmdline-tools" "$ANDROID_SDK/cmdline-tools/latest"
      chmod +x "$CMDLINE_TOOLS/"* 2>/dev/null
    fi
  fi
  if [ -f "$CMDLINE_TOOLS/sdkmanager" ]; then
    log "[phone] Installing Android platform-tools + $PLATFORM_VER + NDK $NDK_VER (this can take a while)..."
    yes | "$CMDLINE_TOOLS/sdkmanager" --licenses >/dev/null 2>&1 || true
    "$CMDLINE_TOOLS/sdkmanager" "platform-tools" "platforms;$PLATFORM_VER" "ndk;$NDK_VER" \
      || warn "[phone] sdkmanager failed - install the SDK manually if you need APK builds."
    export ANDROID_HOME="$ANDROID_SDK" ANDROID_SDK_ROOT="$ANDROID_SDK"
  else
    warn "[phone] sdkmanager unavailable - Android build path skipped."
  fi

  if command -v gradle >/dev/null 2>&1; then
    [ ! -f "$ROOT/LoOper/phone_client/android/gradlew" ] && \
      (cd "$ROOT/LoOper/phone_client/android" && gradle wrapper) || true
  else
    warn "[phone] Gradle not found - run 'gradle wrapper' in LoOper/phone_client/android later."
  fi
}
ensure_phone_prereqs || warn "[phone] prerequisites step had failures - continuing (phone builds only affected)."

# ============================================================================
#  1. Virtual environment + Python dependencies (with graceful degradation)
# ============================================================================
if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "[setup] Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR" || {
    warn "python3-venv may be missing (Debian/Ubuntu): sudo apt install python3-venv python3-dev"
    exit 1
  }
fi
VPY="$VENV_DIR/bin/python"

log "[setup] Upgrading pip..."
"$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || warn "pip upgrade failed - using bundled pip."

log "[setup] Installing Python dependencies (pip install -e .)..."
PIP_OK=1
(cd "$ROOT" && "$VPY" -m pip install -e .) || PIP_OK=0
if [ "$PIP_OK" = 1 ]; then
  log "[setup] Installing pinned lock (build_pipeline/requirements.txt)..."
  (cd "$ROOT" && "$VPY" -m pip install -r "$ROOT/build_pipeline/requirements.txt") || PIP_OK=0
fi

if [ "$PIP_OK" = 0 ]; then
  warn "[setup] Full install failed - switching to LINUX DEGRADED mode:"
  warn "        windows-only packages are skipped and dumped to the report."
  write_probe
  "$PYTHON" "$WIN_TMP/win_probe.py" "$ROOT" "$WIN_TMP/requirements_linux.txt" \
    || { echo "[error] probe failed - fix pip manually." >&2; exit 1; }

  log "[setup] Installing linux-safe subset from cleaned requirements..."
  if ! "$VPY" -m pip install -r "$WIN_TMP/requirements_linux.txt"; then
    warn "[setup] Batch install still failing - isolating per package (slow, one-shot)."
    while read -r spec; do
      [ -z "$spec" ] && continue
      case "$spec" in ./*|-e*|file:*) continue;; esac
      if ! "$VPY" -m pip install -- "$spec" > "$TMPDIR_D/pkg.log" 2>&1; then
        first="$(grep -m1 -iE 'error|failed' "$TMPDIR_D/pkg.log" | tr '\t' ' ' | cut -c1-300)"
        printf '%s\t%s\n' "$spec" "$first" >> "$WIN_ISOLATED"
        warn "  [fail] $spec - skipped (logged to report)"
      fi
    done < "$WIN_TMP/requirements_linux.txt"
  fi

  # The editable package itself, minus the windows deps the probe found
  log "[setup] Installing the arrow package itself (no-deps, sources stay live via -e)..."
  (cd "$ROOT" && "$VPY" -m pip install -e . --no-deps) \
    || warn "[setup] Editable install of arrow failed - app modules still importable from repo root."
fi

write_report "$WIN_TMP/win_probe.py"

# ============================================================================
#  1b. Vendored library patches (same as the .bat)
# ============================================================================
log "[patch] Applying vendored library patches..."
"$VPY" "$ROOT/LoOper/patches/apply_patches.py" \
  || warn "[patch] patch step reported stale files - see LoOper/patches."

# ============================================================================
#  2. llama.cpp engine: existing build -> source build -> prebuilt linux
# ============================================================================
LLAMA_SRC="$ROOT/utils/llama.cpp"
LLAMA_BUILD="$LLAMA_SRC/build"
LLAMA_BIN="$LLAMA_BUILD/bin"

stage_llama() {  # stage executables + shared libs (Linux mirror of the .exe/.dll copy)
  mkdir -p "$AI_BIN"
  log "[llama] Staging binaries from $1 -> $AI_BIN"
  find "$1" -maxdepth 1 -type f \( -name 'llama-*' -o -name 'libllama*' -o -name 'libggml*' \) \
    -exec cp -f {} "$AI_BIN" \; 2>/dev/null
  chmod +x "$AI_BIN"/llama-* 2>/dev/null || true
}

get_prebuilt_llama() {
  log "[llama] Fetching a prebuilt llama.cpp LINUX release (no compiler needed)..."
  local zip host url
  zip="$TMPDIR_D/llama-prebuilt.zip"
  rm -f "$zip"
  case "$(uname -m)" in
    x86_64)        host="linux-x64" ;;
    aarch64|arm64) host="linux-arm64" ;;
    *) warn "[llama] Unsupported CPU ($(uname -m)) - prebuilt skip."; return 0 ;;
  esac
  if ! command -v curl >/dev/null 2>&1; then
    warn "[llama] curl unavailable - cannot look up prebuilt releases."
    return 0
  fi
  # Scan the release list for the first asset that ships the linux binary zip
  # (releases/latest is source-only; the -bin-*.zip assets live on b#### nightlies).
  curl -sL --fail -H 'User-Agent: arrow-setup' \
    "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=40" \
    -o "$TMPDIR_D/llama-rel.json" || true
  url="$(python3 - "$TMPDIR_D/llama-rel.json" "$host" <<'PYEOF'
import json, re, sys
try:
    rels = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
host = sys.argv[2]
for r in rels:
    for a in r.get("assets", []):
        if re.search(r"-bin-%s\.zip$" % re.escape(host), a["name"]):
            print(a["browser_download_url"])
            sys.exit(0)
sys.exit(1)
PYEOF
)" || true
  if [ -z "${url:-}" ]; then
    warn "[llama] Prebuilt download unavailable - the app can still fall back to Ollama."
    return 0
  fi
  log "[llama] Using asset: $(basename "$url")"
  getfile "$url" "$zip"
  if [ ! -f "$zip" ]; then
    warn "[llama] Prebuilt download unavailable - the app can still fall back to Ollama."
    return 0
  fi
  rm -rf "$TMPDIR_D/llama-prebuilt"; mkdir -p "$TMPDIR_D/llama-prebuilt" "$LLAMA_BIN"
  case "$zip" in
    *.tar.gz) tar -xzf "$zip" -C "$TMPDIR_D/llama-prebuilt" ;;
    *.zip)    unzip -q "$zip" -d "$TMPDIR_D/llama-prebuilt" ;;
  esac
  local src
  src="$(find "$TMPDIR_D/llama-prebuilt" -name 'llama-server' -type f | head -1 | xargs -r dirname)"
  if [ -n "${src:-}" ]; then
    cp -f "$src"/* "$LLAMA_BIN"/ 2>/dev/null
    log "[llama] Prebuilt staged at $LLAMA_BIN"
    stage_llama "$LLAMA_BIN"
  else
    warn "[llama] Prebuilt extraction failed - llama.cpp unavailable (Ollama fallback still works)."
  fi
  rm -f "$zip"
}

build_llamacpp() {
  if [ -x "$LLAMA_BIN/llama-server" ]; then
    log "[llama] Existing build found - staging binaries."
    stage_llama "$LLAMA_BIN"; return 0
  fi
  if [ ! -f "$LLAMA_SRC/CMakeLists.txt" ]; then
    log "[llama] Source not found at $LLAMA_SRC - using a prebuilt release."
    get_prebuilt_llama; return 0
  fi
  if ! command -v cmake >/dev/null 2>&1 || { ! command -v cc >/dev/null 2>&1 && ! command -v g++ >/dev/null 2>&1; }; then
    log "[llama] CMake/C++ compiler unavailable - using a prebuilt llama.cpp release instead."
    get_prebuilt_llama; return 0
  fi
  local backend=""
  if [ -d "/usr/share/vulkan" ] || [ -n "${VULKAN_SDK:-}" ]; then
    log "[llama] Vulkan detected - enabling the Vulkan backend."
    backend="-DGGML_VULKAN=ON"
  else
    log "[llama] No Vulkan SDK - building CPU-only."
  fi
  log "[llama] Configuring..."
  if ! cmake -S "$LLAMA_SRC" -B "$LLAMA_BUILD" -DCMAKE_BUILD_TYPE=Release \
       $backend -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF; then
    log "[llama] CMake configure failed - falling back to a prebuilt release."
    get_prebuilt_llama; return 0
  fi
  log "[llama] Building (this can take several minutes)..."
  cmake --build "$LLAMA_BUILD" --parallel "$(nproc)" || warn "[llama] Build reported errors - continuing."
  if [ -x "$LLAMA_BIN/llama-server" ]; then
    stage_llama "$LLAMA_BIN"
  else
    log "[llama] Build produced no llama-server - falling back to a prebuilt release."
    get_prebuilt_llama
  fi
}
build_llamacpp

# ============================================================================
#  3. Base models + browser (identical pinned revisions as the .bat)
#       (getfile / untar helpers are defined above, used by steps 0c and 3)
# ============================================================================
download_models() {
  log "[models] Fetching base models..."

  # Laya decision engine GGUF models (pinned revisions, identical to the .bat)
  getfile "https://huggingface.co/mys/laya-GGUF/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_ud_q4_k_m.gguf" "$LAY_DIR/laya_english_ud_q4_k_m.gguf"
  getfile "https://huggingface.co/mys/laya-GGUF/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_q8_0.gguf" "$LAY_DIR/laya_english_q8_0.gguf"

  # Laya engine BINARY: the ggmlc release ships laya-windows-x86_64-vulkan.zip only.
  if [ -f "$AI_BIN/laya.exe" ] || [ -f "$AI_BIN/laya" ]; then
    echo "  [skip] laya binary"
  else
    printf '%s\t%s\t%s\t%s\n' "laya-windows-x86_64-vulkan.zip (laya.exe)" "Windows-only binary" "no linux build published by ggmlc release v0.9.6" "Arrow_setup.bat :get_laya_exe" >> "$WIN_SKIPPED"
    warn "  [win ] laya.exe is Windows-only - recorded in $WIN_REPORT (Ollama/llama.cpp fallback still works)."
  fi

  getfile "https://huggingface.co/unsloth/embeddinggemma-300m-GGUF/resolve/6661a6504c30d8304af13455cb4a5d4f5bc6011f/embeddinggemma-300M-Q8_0.gguf" "$AI_MODELS/embeddinggemma-300M-Q8_0.gguf"
  getfile "https://huggingface.co/ggml-org/SmolLM3-3B-GGUF/resolve/4965cb60b150737b68a0408c36aeefb65078f894/SmolLM3-Q4_K_M.gguf" "$AI_MODELS/SmolLM3-Q4_K_M.gguf"
  getfile "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/LFM2.5-VL-450M-Q8_0.gguf" "$LFM_DIR/LFM2.5-VL-450M-Q8_0.gguf"
  getfile "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/mmproj-LFM2.5-VL-450m-Q8_0.gguf" "$LFM_DIR/mmproj-LFM2.5-VL-450m-Q8_0.gguf"
  getfile "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF/resolve/aac6aefe07703a6c994fab05828cfa5524609380/locateanything-3b-q8_0.gguf" "$AI_MODELS/LocateAnything-3B-Q8_0.gguf"
  getfile "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF/resolve/aac6aefe07703a6c994fab05828cfa5524609380/mmproj-locateanything-3b-bf16.gguf" "$AI_MODELS/mmproj-LocateAnything-3B-BF16.gguf"

  get_paddle_models
  get_vosk_model
  get_piper_voices
  get_chrome
  log "[models] Base models ready."
}

get_vosk_model() {
  if [ -d "$VOSK_DIR/vosk-model-small-en-us-0.15" ]; then echo "  [skip] vosk model"; return 0; fi
  getfile "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" "$TMPDIR_D/vosk.zip"
  [ -f "$TMPDIR_D/vosk.zip" ] || return 0
  echo "  [pkg ] extracting Vosk model"
  mkdir -p "$VOSK_DIR"
  unzip -q -o "$TMPDIR_D/vosk.zip" -d "$VOSK_DIR"
}

get_piper_voices() {
  getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" "$PIPER_DIR/en_US-lessac-medium.onnx"
  getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" "$PIPER_DIR/en_US-lessac-medium.onnx.json"
  getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx" "$PIPER_DIR/es_ES-carlfm-x_low.onnx"
  getfile "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx.json" "$PIPER_DIR/es_ES-carlfm-x_low.onnx.json"
}

get_chrome() {
  # Chrome for Testing + matching chromedriver - linux64 assets of the SAME
  # pinned version the .bat uses (win64). Falls back gracefully per-URL.
  local CHROME_DIR="$AI_BIN/chrome" CFT_VER="153.0.8010.48" base
  if [ -x "$CHROME_DIR/chrome" ] && [ -x "$CHROME_DIR/chromedriver" ]; then
    echo "  [skip] $CHROME_DIR"; return 0
  fi
  base="https://storage.googleapis.com/chrome-for-testing-public/$CFT_VER/linux64"
  log "[chrome] Chrome for Testing $CFT_VER (pinned, linux64)"
  getfile "$base/chrome-linux64.zip" "$TMPDIR_D/cft-chrome.zip" || true
  if [ ! -f "$TMPDIR_D/cft-chrome.zip" ]; then
    printf '%s\t%s\t%s\t%s\n' "Chrome for Testing $CFT_VER win64 + chromedriver.exe" "Windows binaries" "linux64 assets unavailable for the pinned version" "Arrow_setup.bat :get_chrome" >> "$WIN_SKIPPED"
    warn "  [warn] Chrome for Testing download failed - skipping (recorded in report)."
    return 0
  fi
  getfile "$base/chromedriver-linux64.zip" "$TMPDIR_D/cft-driver.zip" || true
  mkdir -p "$AI_BIN"
  echo "  [pkg ] extracting Chrome for Testing"
  rm -rf "$TMPDIR_D/cft_chrome"; unzip -q "$TMPDIR_D/cft-chrome.zip" -d "$TMPDIR_D/cft_chrome"
  mkdir -p "$CHROME_DIR"
  cp -a "$TMPDIR_D/cft_chrome/chrome-linux64/." "$CHROME_DIR/"
  if [ -f "$TMPDIR_D/cft-driver.zip" ]; then
    rm -rf "$TMPDIR_D/cft_driver"; unzip -q "$TMPDIR_D/cft-driver.zip" -d "$TMPDIR_D/cft_driver"
    cp -f "$TMPDIR_D/cft_driver/chromedriver-linux64/chromedriver" "$CHROME_DIR/chromedriver"
  fi
  chmod +x "$CHROME_DIR/chrome" "$CHROME_DIR/chromedriver" 2>/dev/null || true
  echo "$CFT_VER" > "$CHROME_DIR/.chrome-version"
  log "[chrome] Chrome for Testing staged at $CHROME_DIR"
}

get_paddle_models() {
  local PADDLE_WHL="$HOME/.paddleocr/whl"
  local PD_DET="$PADDLE_WHL/det/en/en_PP-OCRv3_det_infer"
  local PD_REC="$PADDLE_WHL/rec/en/en_PP-OCRv4_rec_infer"
  local PD_CLS="$PADDLE_WHL/cls/ch_ppocr_mobile_v2.0_cls_infer"
  if [ ! -d "$PD_DET" ]; then
    getfile "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar" "$TMPDIR_D/det.tar"
    if [ -f "$TMPDIR_D/det.tar" ]; then
      echo "  [pkg ] extracting en_PP-OCRv3_det_infer"; mkdir -p "$PADDLE_WHL/det/en"
      untar "$TMPDIR_D/det.tar" "$PADDLE_WHL/det/en"
    fi
  fi
  if [ ! -d "$PD_REC" ]; then
    getfile "https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar" "$TMPDIR_D/rec.tar"
    if [ -f "$TMPDIR_D/rec.tar" ]; then
      echo "  [pkg ] extracting en_PP-OCRv4_rec_infer"; mkdir -p "$PADDLE_WHL/rec/en"
      untar "$TMPDIR_D/rec.tar" "$PADDLE_WHL/rec/en"
    fi
  fi
  if [ ! -d "$PD_CLS" ]; then
    getfile "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar" "$TMPDIR_D/cls.tar"
    if [ -f "$TMPDIR_D/cls.tar" ]; then
      echo "  [pkg ] extracting ch_ppocr_mobile_v2.0_cls_infer"; mkdir -p "$PADDLE_WHL/cls"
      untar "$TMPDIR_D/cls.tar" "$PADDLE_WHL/cls"
    fi
  fi
}

download_models

# ============================================================================
#  4. RDP sandbox: WINDOWS-ONLY (RDPWrap / net user / reg / cmdkey / netsh)
# ============================================================================
log "[rdp] The RDPWrap sandbox from Arrow_setup.bat is Windows-only (SAS/Session 0 trick)."
log "[rdp] On Linux use a dedicated user + X/Wayland session or a VM snapshot instead - skipped."
printf '%s\t%s\t%s\t%s\n' "RDPWrap v1.6.2 (RDPWInst.exe), LoOperAgent local user, TSAppAllowList registry, netsh RDP firewall rules" "Windows-only subsystem" "no Linux equivalent - Windows Remote Desktop stack" "Arrow_setup.bat step 4" >> "$WIN_SKIPPED"
# refresh the report with anything appended during steps 2-4
write_report "$WIN_TMP/win_probe.py"

# ============================================================================
#  5. Launch - Debug (terminal visible) or Normal (no console)
# ============================================================================
echo
echo "  How do you want to start arrow?"
echo "    [D] Debug  - LoOper/main.py with the terminal visible (logs / tracebacks)"
echo "    [N] Normal - LoOper/main.py detached from the terminal"
read -r -p "   Choose [D]ebug or [N]ormal [D/N] (default D): " ANSWER
case "${ANSWER:-D}" in
  [nN]*)
    log "[run] Normal mode - launching detached..."
    setsid "$VPY" "$ROOT/LoOper/main.py" >/dev/null 2>&1 < /dev/null &
    ;;
  *)
    log "[run] Debug mode - terminal visible. Close this terminal to stop the app."
    PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1 exec "$VPY" "$ROOT/LoOper/main.py"
    ;;
esac
