# Arrow

**Your team of AI agents — on your own computer.**

Meet Arrow: the automation platform that turns your Windows PC into a workplace for AI teammates you can actually trust. Give a teammate a name, a job, and a set of skills. Message it like a colleague. Show it how you like things done — it records what you do, learns your way of working, and finishes the job while you do something else.

No cloud computers. No per-message fees. No vendor between you and your data. Every bot you build is a file on your machine, and every step it takes is something you can watch, audit, stop, or change.

---

## What makes Arrow different

Most personal-bot services rent you time on *their* computer, in *their* cloud, with *their* models. Arrow inverts that:

| Hosted AI bots | Arrow |
|---|---|
| A bot profile on someone else's server | **The bot is a file on your machine** — open it, edit it, copy it, export it |
| Works in cloud sandboxes, not your actual tools | **Works inside your real apps** — clicks, types, and browses the Windows programs and websites you already use |
| Your data leaves your machine | **Fully on-device** — local models, local vision, local speech. Works offline and in air-gapped rooms |
| Pay per message or per seat forever | **Free for individuals** — every feature, every node type, no execution limits |
| You can't see what it's doing under the hood | **Every step is auditable** — live trace, per-step verdicts, activity timeline, one Esc key away from stopping it |
| Personality locked to a settings page | **You shape the brain and the hands** — draw the logic as a graph, wire up its tools, change it whenever you want |

---

## What an Arrow teammate can do

**🧠 You build its brain by talking to it.**
Open the Builder chat and say *"make me a bot that finds job offers on LinkedIn, applies to the good ones, and keeps a spreadsheet."* Arrow scaffolds the whole agent as a visual graph on your canvas — while you watch it take shape and edit anything directly. No code required.

**✋ You show it your hands.**
Hit record, do a task once — a browser session, a desktop routine, a form you fill every week. That recording becomes a *tool* your agent can use forever, replayed element-by-element so it survives layout changes. Don't explain. Demonstrate.

**🧠 It reasons, it doesn't guess.**
Every agent runs on a visual workflow of typed nodes: reasoning steps, memory, decisions, tools. Fuzzy judgement sits only where it belongs; everything else is deterministic. If a model answers badly, the bot fails safely instead of acting on a guess.

**👥 Work with a whole team.**
Make several agents — a chief of staff and a specialist for each lane: inbox, applications, research, reports. The Orchestrator brain loops toward a goal, hands steps to the right specialist, verifies each one against the real screen, and only comes back to you when something needs your approval.

**📈 It gets sharper the more you use it.**
When a run ends with every step verified, Arrow *freezes it into a learned routine* — a file you can open, edit, or delete. Rate its answers, and it rewords its own tool descriptions. Context memory compounds across sessions. Your bot picks up your voice, your edge cases, and when to ask instead of act.

**📱 Message it from anywhere.**
The Android phone app reaches your PC over a peer-to-peer link by key — **no IP address, no VPN, no exposed port**. Start a run on desktop, answer its questions from your phone, hear the results spoken aloud. Notifications arrive per message.

**🌙 It keeps working while you sleep.**
Schedule any routine — hourly, daily, weekly. Persistent goals park themselves when blocked and resume on the next wake. It works when your laptop lid is closed.

**🔍 It can see, read, and speak.**
Vision-language understanding lets it click anything on screen by looking at it. PaddleOCR reads text, Piper speaks results, Vosk hears your voice. All local.

**🗝️ Your models or theirs.**
Runs on bundled local models (llama.cpp, Ollama) by default. Prefer a bigger brain? BYOK plugs in your own keys — OpenAI-compatible providers, local model servers, CLI tools — per node or per agent. The build never depends on them.

**📦 Ship your teammate.**
Compile any agent — its brain plus every tool and model it needs — into a standalone `.exe`. Your bot, in a box, running anywhere on Windows.

---

## Quick start

### Install

**Option A — one-shot source setup (recommended for development):**

```powershell
# 1. Clone the repository
git clone <your-repo-url>
cd LoOperV2

# 2. Run the bootstrap — it installs Python 3.11, the dependencies,
#    the llama.cpp engine, and all base models, then launches the app.
.\Arrow_setup.bat

# Skip phone-client prerequisites if you don't build the Android app:
set SKIP_PHONE=1
.\Arrow_setup.bat
```

`Arrow_setup.bat` is the whole setup: it pulls the latest source, installs Python (winget, else python.org), creates the virtual environment, builds or downloads the llama.cpp engine (no compiler required — a prebuilt is fetched when a build isn't possible), downloads the base models and the bundled Chrome, and launches `LoOper\main.py`. **No administrator rights needed.** The optional RDP sandbox step self-skips when not elevated.

**Option B — packaged installer:**

The WiX MSI (`build_pipeline/build_msi.ps1`) installs the app per-user with a custom UI. The installer builds the llama.cpp engine **on your machine**, so it always matches your CPU.

### Your first automation — five steps

1. **Launch Arrow** — the node-graph canvas opens.
2. **Record** — drop a *Sequence* node (desktop) or *Web Sequence* node (browser) on the canvas, hit record, and do a task once.
3. **Connect** — wire the recording to an *Input* node and an *Output* node.
4. **Run** — press play. It replays the task with human-like pacing, re-finding every element even if windows moved.
5. **Repeat** — every routine you record becomes a tool you can wire into any agent later.

### Your first agent — talk to the builder

1. Click the **Builder** button on the canvas toolbar — the chat opens on the right, the canvas stays visible on the left.
2. Type: *"make me a bot that tracks prices on three stores and tells me when one drops."*
3. Watch it build: it decomposes the goal into tools, matches the routines you already recorded, and — for anything missing — **drives the browser itself to demonstrate the step**, or asks you to record it.
4. Fix anything by hand on the canvas. It's your bot.
5. Open **File → Agent** and chat with it. Ask-user questions arrive as buttons; you can answer from the phone too.

### Take it to your phone

1. Build the APK: `.\build_pipeline\build_phone_apk.ps1` (prerequisites installed by `Arrow_setup.bat`), then `adb install -r android\app\build\outputs\apk\debug\app-debug.apk`.
2. In agent mode, open the QR code (`pear://<key>`) and scan it with the app.
3. Done — chat, answer questions, hear answers, and stop runs from anywhere. No VPN, no IP address.

### Going further

- **Schedule it** — any chain can run once, daily, weekly, or on an interval.
- **Teach it to learn** — let the Orchestrator verify a successful run; it freezes into a *learned routine* under `LoOper/chains/learned/`.
- **Export it** — compile a full agent into a standalone `.exe` from the export dialog.
- **Docs** — [User Guide](docs/USER_GUIDE.md) · [Node Runbook](docs/NODE_RUNBOOK.md) · [Phone client](LoOper/phone_client/README.md) · [Technical deep dive](LoOper/README.MD).

---

## Built for privacy, priced for everyone

- **Individual use is free.** Every automation feature and node type, no execution limits.
- **Offline and telemetry-free.** All inference, storage, and licensing work without network access. Nothing leaves your machine.
- **Licensing without activation servers** — offline HMAC-signed files bound to your hardware (`keygen/`).
- **Agent export** — compiling distributable agent executables — is the enterprise right.

---

*Arrow (internal package name: LoOper) — record it once, hand it off forever.*
