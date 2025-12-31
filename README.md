# LoOper — Advanced Desktop Automation Platform

LoOper is a desktop-native automation platform for recording, building, and running reliable workflows that can *see* the screen, *reason* with local AI, and *act* through mouse/keyboard control. It combines a visual node-graph editor with a modular recorder/player engine, plus an optional local API layer for LLM + RAG orchestration.

## 🎉 First Release — v1.0.0 (2025-12-31)

Today marks the first official release of **LoOper v1.0.0**.

### What’s included in v1.0.0

- **Visual workflow builder (NGUI)**: a PyQt5 + NodeGraphQt editor to assemble automations as node graphs.
- **Recorder**: captures clicks, drags, scroll bursts, and typed text into JSON sequences with auto screenshots.
- **Player**: plays sequences and full workflow chains with retries, fallbacks, and ESC-to-cancel.
- **Computer vision + OCR**: template matching for reliable clicks; optional OCR-powered conditions.
- **Local AI integration**:
  - **LLM nodes** (Ollama) with optional vision models and typed output automation.
  - **ComoRAG** support via a local API server for private, local-first retrieval + reasoning.
- **Scheduler**: run chains on schedules (once/daily/weekly/interval) and persist to `schedules.json`.
- **Windows packaging**:
  - **PyInstaller build spec** for a GUI executable.
  - **WiX MSI builder** (`generate_wxs.py`, `build_msi.ps1`) for an installer.
  - **Post-install dependency helper** (`install_dependencies.ps1`) to install Ollama and verify OCR models.

## 🧩 Main Components

- **NGUI (Node Graph Editor)**: author and manage workflow chains with node-based logic, dialogs, and graph operations.
- **Recorder**: records user interactions into a reproducible sequence format.
- **Player**: executes sequences and workflow graphs with vision/OCR/AI capabilities.
- **AI layer (optional)**:
  - **LoOper local API server** (FastAPI + Uvicorn) for LLM + ComoRAG endpoints.
  - **OverWatch**: a standalone API + GUI for model management and ComoRAG reasoning.
- **Scheduler (optional)**: background service to launch chains by time schedule.

## ✅ Functionality Overview

### 🎨 Visual Workflow Builder (NGUI)

- **Node graph authoring** with zoom/pan, selection, and auto-layout.
- **Context-menu editing**: node configuration lives in dialogs to keep nodes visually clean.
- **Node transforms**: convert between Sequence / Conditional / LLM node types where supported.
- **Dark UI theme** with consistent port colors (inputs blue, outputs green; conditional true/false differentiated).

### 🧱 Node Types

- **Sequence Node**: execute a recorded sequence file, with loop count and extra delay options.
- **Conditional Node**:
  - Presence/absence triggers (image-based).
  - Wait conditions with timeouts and retry behavior.
  - OCR-based triggers (full-screen OCR; configurable target text and sensitivity).
  - Loop-style condition variants (while/until patterns).
- **LLM Node**:
  - Local Ollama models by default.
  - Optional vision model support.
  - Optional ComoRAG usage (local-first retrieval/reasoning).
  - Output can be stored as variables and optionally typed into the active app.
- **TTS Node**: speak notifications (and optionally consume variables in the message).
- **Chain Import Node**: compose chains by importing another chain JSON and executing it as a subgraph.
- **Code Node**: execute Python code during a workflow (inline code or file path, timeout, output variable).
- **Form Filler Node**: Under development.

### 📹 Recorder

- **Captures**: clicks (with screenshots), drags, scrolling, keyboard input.
- **Smart grouping**:
  - Scroll bursts become a single scroll action.
  - Typed text buffers into a single type action.
  - Common modifier shortcuts are recognized (Ctrl+C/V/X/A, etc.).
- **Outputs**: JSON sequence files + a `screenshots/` folder for template-based playback.

### ▶️ Player

- **Plays sequences** (`{"actions": [...]}` JSON).
- **Plays workflow chains** (dict-based chain format with sequences + node graph metadata).
- **Robust execution**:
  - Template matching with confidence thresholds and region strategies.
  - Fallback strategies when matching fails (eg. retrying, alternate behaviors).
  - ESC key cancellation for safe abort.

### 👁️ Vision + OCR

- **Template matching** (OpenCV) for finding UI targets reliably even when coordinates shift.
- **OCR integration** for condition checks (eg. “wait until text appears”).

### 🤖 Local AI (Ollama + ComoRAG)

- **Ollama-first** workflow nodes for local, offline-friendly inference.
- **Local API server** can be auto-started from the GUI process (configurable).
for:
  - Model management and status monitoring.
  - ComoRAG reasoning and memory inspection.
  - Secure networking via optional Tailscale configuration.

### ⏰ Scheduling

- Create schedules for chains: **once**, **daily**, **weekly**, **interval**.
- Persist schedules to `schedules.json` and compute `next_run` automatically.
- Launches chain execution in the background as a subprocess.

### 📦 Packaging & Installer (Windows)

- **PyInstaller spec** for bundling GUI + vision/OCR/AI deps.
- **MSI build** support using WiX (v4+) with shortcut creation and a post-install dependency action.
- **Dependency bootstrap**:
  - Installs Ollama silently if missing.
  - Verifies/initializes OCR model assets via the bundled app.

## 📁 Repository Layout (High-Level)

```
LoOperV2/
├── LoOper.py                 # Launcher (splash + main app process)
├── LoOper/                   # Application package
│   ├── main.py               # GUI entrypoint + API auto-start
│   ├── NGUI/                 # Node graph editor (PyQt5 +  NodeGraphQt)
│   ├── recorder/             # Input recording modules
│   ├── player/               # Playback engine (sequence + workflow graphs)
│   └── AI/                   # Local API config + model helpers 
├── install_dependencies.ps1  # Installs/validates Ollama + OCR assets (installer helper)
├── LoOperApp.spec            # PyInstaller build spec
├── generate_wxs.py           # WiX .wxs generator (MSI)
├── build_msi.ps1             # MSI builder script (WiX)
└── tests/                    # Unit tests (unittest)
```

## 🚀 Quick Start (From Source)

### Requirements

- **Windows** is the primary supported platform for desktop automation.
- **Python 3.10+** (project dependencies are defined in `pyproject.toml`).
- **Ollama** recommended for local LLM nodes.

### Install

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -e .
```

### Run

```bash
python LoOper.py
```

## ⚙️ Configuration (AI / API)

LoOper reads AI settings from `LoOper/AI/config.json` and selected environment variables:

- `AUTO_START_API` (`true`/`false`)
- `API_PORT` (default `8000`)
- `TAILSCALE_SERVER_IP` (leave empty for `localhost`)
- `OLLAMA_HOST` / `OLLAMA_PORT`
- `USE_COMORAG` (`true`/`false`)
- `OVERWATCH_API_URL` (optional override) #UNDER DEVELOPMENT

## 🗂️ File Formats (Conceptual)

- **Sequence JSON**: contains an `actions` list plus metadata; screenshots are stored alongside in `screenshots/`.
- **Chain / Workflow JSON**: contains workflow nodes and connections (sequences, conditionals, LLM, TTS, code, imported chains).

## 🧯 Operational Notes

- **Safety**: workflows can be stopped during playback via **ESC**.
- **Privacy-first**: the architecture is designed to run locally; remote Inference is optional (eg. Tailscale) and user-controlled.
