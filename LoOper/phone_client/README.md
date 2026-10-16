# LoOper Phone Client

Standalone **Pear / Bare** app for **LoOper Agent Mode** (Android). It dials the
LoOper host by its Ed25519 public key over `hyperdht` and exposes the connection
on a loopback port. The chat UI is **bundled in the APK**
(`android/app/src/main/assets/web/index.html`) and drives the host's transport
API (`/ws`, `/stt`, `/tts`) through the pipe — so the phone controls the agent
from anywhere, with no server-served page.

This folder is a **companion app**, not part of the Python / NGUI app. The host
half is `LoOper/agent_sidecar/` (the P2P transport, run by Node.js). Since the UI
now lives here, `agent_web_server.py` serves only the API (no HTML).

## Layout — one core, many shells

```
core/connect.js    dials the host by key, byte-pipes to 127.0.0.1:<port>
core/entry.js      reads the key from Bare.argv, reports readiness (IPC/stdout)
android/           Android app: Kotlin bare-kit Worklet + WebView
  app/src/main/assets/web/index.html   the chat UI (bundled; served over https)
  app/build.gradle          AGP config + `link` / `pack` tasks (wired into preBuild)
  app/src/main/java/...     MainActivity.kt (worklet + WebView + QR scanner)
                            NotifyService.kt (foreground service + notifications)
  app/src/main/AndroidManifest.xml
package.json       hyperdht, bare-net, b4a (+ dev bare-link, bare-pack)
pear.json          Pear app manifest (enables `pear .` on desktop)
```

**Generated (git-ignored, recreated by the build):** `node_modules/`,
`android/.gradle/`, `android/**/build/`, `android/local.properties`,
`android/gradlew*` + `android/gradle/` (wrapper), `android/app/libs/bare-kit/`
(prebuild), `android/app/src/main/addons/` (linked `.so`), and
`android/app/src/main/assets/app.bundle`.

## Prerequisites

One shot from the repo root — installs Node.js, JDK 17, GitHub CLI, Android
Studio, the Android SDK command-line tools + platform 35 + NDK 27.2.12479018, a
portable Gradle, and generates the project's Gradle wrapper:

```powershell
.\Arrow_setup.bat          # set SKIP_PHONE=1 first to skip the phone prerequisites
```

No environment variables are needed afterwards: `build_phone_apk.ps1`
auto-detects the SDK (`%LOCALAPPDATA%\Android\Sdk`), the JDK 17 install, and
`.tools\gradle`.

## Build the Android APK

From the repo root (Windows PowerShell — `pwsh` is not required):

```powershell
.\build_pipeline\build_phone_apk.ps1                       # debug APK
.\build_pipeline\build_phone_apk.ps1 -Configuration Release
.\build_pipeline\build_phone_apk.ps1 -SkipNpmInstall       # deps already installed
```

Output: `android/app/build/outputs/apk/debug/app-debug.apk`

The script checks prerequisites, fetches the `bare-kit` prebuild via `gh`
(unless already present), runs `npm install`, then `gradle assemble<Debug|Release>`.
The Gradle `preBuild` runs `link` (bare-link → native addons) and `pack`
(bare-pack → `app.bundle`).

Install on a device:

```powershell
adb install -r android\app\build\outputs\apk\debug\app-debug.apk
```

## Run on desktop (no shell needed)

```sh
cd LoOper/phone_client
npm install
npm start -- <64-char-host-pubkey>     # prints {"type":"ready","port":8081}
# then open http://127.0.0.1:8081/ in a browser
```

`bare` must be on `PATH`. A Pear app manifest (`pear.json`) is included, so
`pear .` also works where the Pear Runtime is installed.

## How the phone connects (no IP)

1. Host publishes `pear://<pubkey>` behind its QR code.
2. The Android app scans the QR (`pear://<pubkey>`, ZXing) or accepts a pasted
   key, stores it, and auto-reconnects on the next launch.
3. It starts a worklet running `core/entry.js` with the pubkey as an argument.
4. The worklet `dht.connect(pubkey)`s the host and byte-pipes the stream to
   `127.0.0.1:<port>`.
5. The WebView loads the **bundled** page, served from a secure asset origin
   (`https://appassets.androidplatform.net/assets/web/index.html` via
   `WebViewAssetLoader`), which opens `ws://127.0.0.1:8081/ws` — piped to the
   host's `/ws` (chat and panels), with `/stt` and `/tts` for voice.

## Notifications + background

The app posts **one system notification per agent message / ask prompt** so you
never have to reopen it to check. To receive messages while the UI is hidden, a
P2P link must stay up (there is no push server), so once connected the app
starts a **foreground service** (`NotifyService`) that keeps the worklet + pipe
alive and shows an ongoing "Connected" notification. Notifications are
suppressed while the UI is in the foreground (nothing to alert about).

Permissions requested at launch: `RECORD_AUDIO` (voice input) and, on Android
13+, `POST_NOTIFICATIONS`.

## Voice

- **Text-to-speech**: every agent output (and every ask question) is spoken via
the host's `/tts`, per message — not only when a run ends.
- **Speech-to-text**: hold (or tap, during an ask) the mic button; audio is
captured at the device rate, resampled to 16 kHz, and POSTed to `/stt`.

## Troubleshooting

- **App closes on connect** — fixed: the worklet now catches JS errors and shows
  them over IPC instead of aborting the host app (a worklet error used to take
  the whole app down). Rebuild + reinstall to pick it up.
- **`UNKNOWN_FLAG: needs`** — `bare-link` 4.x removed `--needs`; the `link` task
  uses `--preset android --out … core/entry.js` only.
- **`SDK XML versions up to 3 …`** — harmless; AGP auto-installs the Build-Tools
  it needs.
- **`Android SDK not found`** — run `Arrow_setup.bat`; the build script
  auto-detects `%LOCALAPPDATA%\Android\Sdk` when the env var is unset.
- **`No Gradle found`** — run `Arrow_setup.bat` (installs portable Gradle +
  wrapper), or run `gradle wrapper` in `android/` with any Gradle.
- **No microphone / voice** — the page must be a secure context for
  `getUserMedia`; it is served over `https://appassets.androidplatform.net`.
  If the mic still fails, check logcat tag `LoOperWeb` for `[mic]` lines (they
  log the exact `getUserMedia` error name).
- **No notifications** — grant the notification permission (Android 13+), and
  keep the ongoing "Connected" notification (the foreground service) running.

## Limits / TODO

- `minSdk = 26`. Raise it in `android/app/build.gradle` if `bare-kit` requires more.
- Cleartext HTTP is enabled app-wide (`usesCleartextTraffic`) for the loopback hop.
- The foreground service uses type `dataSync`, which Android 15+ caps at ~6h per
  day; a permanently-running link would need a `specialUse` declaration.
- Trust-on-first-use: any key typed is accepted. Add an allow-list check against
  a pinned host key before shipping.
- iOS shell not built yet — the same `core/` packs as a worklet + `WKWebView`.
- Package version ranges are best-effort; bump with `npm install <pkg>@latest`
  if a range fails to resolve.
