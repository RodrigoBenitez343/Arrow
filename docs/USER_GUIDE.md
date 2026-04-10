# LoOper User Guide

**Looper** is a desktop-native automation platform for recording, building, and running reliable workflows that can *see* the screen, *reason* with local AI, and *act* through mouse/keyboard control.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Recording Sequences](#recording-sequences)
3. [Playback and Execution](#playback-and-execution)
4. [Schedules](#schedules)
5. [Node Types](#node-types)
6. [Recording Special Actions](#recording-special-actions)
7. [Node Dialogs Reference](#node-dialogs-reference)
8. [Sandboxing](#sandboxing)

---

## Getting Started

<!-- IMAGE PLACEHOLDER: Main application window screenshot -->

When you launch Looper, you will see the main window with:

- **Central Canvas**: The node graph editor where you build automation workflows
- **Top Menu Bar**: File operations, settings, and tools
- **Right Toolbar**: Node palette with available node types
- **Node Graph**: Visual representation of your automation workflow

### Project Structure

Looper organizes your work into:

| Folder | Purpose |
|--------|---------|
| `sequences/` | Stored recorded sequences (JSON + screenshots) |
| `chains/` | Chain workflow files that combine sequences and nodes |

---

## Recording Sequences

Recording is the process of capturing your mouse clicks, keyboard input, and scrolling into a reusable sequence file.

### How to Record

1. Click **Record** in the toolbar or use the menu option
2. Perform your desired actions on screen:
   - Clicks (left, right, middle)
   - Keyboard typing
   - Scrolling
   - Drag operations
3. Press **ESC** or click **Stop** to end recording
4. Your sequence is saved automatically to the `sequences/` folder

### Recording Settings

| Setting | Description |
|---------|-------------|
| Sequence Name | Name for the recorded sequence file |
| Screenshots Directory | Where click screenshots are stored |
| App Context | Automatically captures which application was active |

<!-- IMAGE PLACEHOLDER: Recording toolbar with record button highlighted -->

### What Gets Recorded

| Action Type | Description |
|-------------|-------------|
| `click` | Mouse clicks with coordinates and screenshots |
| `type_string` | Typed text (batched for efficiency) |
| `keystroke` | Special keys (Enter, Tab, etc.) |
| `clipboard` | Copy (Ctrl+C), Paste (Ctrl+V), Cut (Ctrl+X), Select All (Ctrl+A), Undo (Ctrl+Z), Redo (Ctrl+Y) |
| `scroll` | Scroll bursts (multiple scrolls grouped into one action) |
| `drag_drop` | Mouse drag operations |
| `move_to` | Mouse movement (when holding Right-Ctrl) |
| `absolute_click` | Mouse left click at specific coordinates (when holding Right-Shift) |

### Smart Recording Features

- **Scroll Burst Grouping**: Multiple rapid scroll events are grouped into a single scroll action
- **Text Buffering**: Typed characters are batched into a single `type_string` action
- **Modifier Shortcuts**: Common shortcuts like Ctrl+C/V/X/A are recognized and recorded as clipboard operations
- **App Context**: Records which application window was active for each action
- **Native Screenshot Capture**: Looper captures screenshots automatically around click coordinates for visual matching during playback

### Screenshot Capture

During recording, Looper automatically captures small screenshots (70x70 pixels by default) around each click location. These screenshots are stored in `sequences/screenshots/` and are used during playback for template matching to find the correct UI element.

<!-- IMAGE PLACEHOLDER: Screenshot capture example showing click region -->

---

## Playback and Execution

All nodes in Looper (not just sequences) are executed through the **Workflow Interpreter**, which handles:

- Sequential execution of nodes in dependency order
- Conditional branching based on true/false outputs
- Error handling and retries
- Variable passing between nodes

### How Playback Works

1. **Start**: The workflow interpreter loads the chain file
2. **Node Execution**: Each node is executed in order based on connections
3. **Branching**: Conditional nodes route execution to different branches
4. **Completion**: Chain finishes or ESC cancels execution

### Visual Matching

Looper uses **template matching** to find UI elements reliably:

| Method | Description |
|--------|-------------|
| Template Match | Finds the screenshot image on screen |
| Multi-Scale | Handles size variations of UI elements |
| Region Search | Limits search to a specific screen area |
| OCR Trigger | Waits for text to appear using optical character recognition |

### Stopping Execution

Press **ESC** at any time to cancel execution safely.

---

## Schedules

Schedules allow you to automatically run your automation chains at specific times.

### Schedule Types

| Type | Description |
|------|-------------|
| **Once** | Run a single time at a specified date/time |
| **Daily** | Run every day at a specified time |
| **Weekly** | Run on specific days of the week |
| **Interval** | Run every N minutes/hours |

### Creating a Schedule

1. Open **Scheduler Manager** from the menu
2. Click **Add Schedule**
3. Configure:
   - **Name**: Descriptive name for the schedule
   - **Type**: Once, Daily, Weekly, or Interval
   - **When**: The time/date specification
   - **Chain**: Select which chain to run
4. Enable/disable the schedule using the toggle

### Schedule Management

| Feature | Description |
|---------|-------------|
| Enable/Disable | Toggle schedules on/off without deleting |
| Run Now | Manually trigger a schedule immediately |
| Edit | Modify schedule parameters |
| Delete | Remove a schedule permanently |

<!-- IMAGE PLACEHOLDER: Scheduler manager dialog -->

### Persistence

Schedules are saved to `schedules.json` and persist across application restarts. The scheduler runs as a background service that checks every 10 seconds for due schedules.

---

## Node Types

Nodes are the building blocks of automation workflows. Each node type performs a specific function.

### Node Colors Quick Reference

| Node Type | Color |
|-----------|-------|
| Sequence Node | Dark Grey (#121212) |
| Conditional Node | Purple (#7B61FF) |
| LLM Node | Orange (#FF6B35) |
| TTS Node | Cyan (#00CED1) |
| Chain Import Node | Green (#32CD32) |
| Form Filler Node | Plum (#DDA0DD) |
| Code Node | Yellow (#F1C40F) |

### 1. Sequence Node

**Purpose**: Executes a recorded sequence file.

| Property | Description |
|----------|-------------|
| `sequence_file` | Path to the sequence JSON file |
| `loop_count` | Number of times to repeat the sequence |
| `extra_delay` | Additional delay between iterations (seconds) |
| `use_app_opened` | Whether to focus the recorded app before playing |

**Color**: Dark Grey
**Ports**: 1 input, 1 output (multi-output capable)

<!-- IMAGE PLACEHOLDER: Sequence node diagram -->

### 2. Conditional Node

**Purpose**: Waits for conditions or creates branching logic based on screen state or code evaluation.

#### Condition Types

| Condition Type | Description |
|----------------|-------------|
| **Code Condition** | Execute Python code that returns True/False |
| **Presence Trigger** | Wait until an image appears on screen (True output), or continue immediately if not found (False output) |
| **OCR Trigger** | Wait until specific text is detected |
| **Layout Match** | Wait for image at a specific screen region with exact position |
| **Wait Time** | Wait for a specified duration |

**Note**: Absence behavior is handled by connecting nodes to the False output of a Presence Trigger, allowing you to branch based on whether an image is found or not.

#### Conditional Loop Types

Looper supports conditional loops that execute a sequence repeatedly based on conditions:

| Loop Type | Description |
|-----------|-------------|
| **while_present** | Loop while an image/condition is present |
| **while_absent** | Loop while an image/condition is absent |
| **until_present** | Loop until an image/condition appears |
| **until_absent** | Loop until an image/condition disappears |

| Property | Description |
|----------|-------------|
| `code` | Python code that must return True/False |
| `code_timeout` | Maximum execution time for code condition |
| `confidence` | Match confidence threshold (0.0-1.0) |
| `timeout` | Maximum wait time (0 = infinite) |
| `max_iterations` | Maximum loop iterations |
| `sequence_file` | Sequence to execute in each loop iteration |
| `iteration_delay` | Delay between loop iterations |

**Color**: Purple (#7B61FF)
**Ports**: 1 input, 2 outputs (True/False for conditionals)

<!-- IMAGE PLACEHOLDER: Conditional node with condition types -->

#### Creating Image Triggers

Looper provides a built-in screenshot capture tool for creating image-based triggers:

1. Click **Capture...** in the trigger dialog
2. Looper minimizes the window and freezes the screen
3. Draw a rectangle around the target area
4. The screenshot is automatically saved and path inserted

<!-- IMAGE PLACEHOLDER: Screen capture snipping tool in action -->

#### Layout Match

Layout Match is a specialized conditional that captures both the image AND its exact screen position. This is useful for:

- UI elements that move between sessions
- Elements that may appear at different locations
- Precise position tracking for subsequent actions

| Property | Description |
|----------|-------------|
| `target_x, target_y` | Captured X,Y position |
| `target_w, target_h` | Captured region dimensions |
| `position_tolerance` | Pixel tolerance for position matching |

### 3. LLM Node

**Purpose**: Integrates local AI (Ollama) for data ingestion, manipulation, dynamic text generation, and analysis. The LLM node is not just for decision making - it supports multiple use cases.

#### Input Sources

| Source | Description |
|--------|-------------|
| None | Direct prompt (no input processing) |
| OCR | Extract text from screen using optical character recognition |
| Previous Node | Use output from the previous node in the chain |
| Clipboard | Use current clipboard content |

#### Model Configuration

| Property | Description |
|----------|-------------|
| `model` | Ollama model to use (e.g., llama3.2:latest) |
| `temperature` | Response randomness (0.0-2.0) |
| `max_tokens` | Maximum response length |
| `api_url` | Ollama API endpoint (defaults to localhost:11434) |

#### Prompt Configuration

| Property | Description |
|----------|-------------|
| `system_message` | Base instructions for the AI behavior |
| `prompt` | User query template |

#### Output Configuration

| Property | Description |
|----------|-------------|
| `output_variable` | Variable name to store response |
| `write_text` | Automatically type generated text |
| `typing_batch_size` | Characters per typing batch |
| `typing_batch_delay` | Delay between typing batches |

#### Vision Features

| Property | Description |
|----------|-------------|
| `use_vision` | Enable image understanding |
| `vision_model` | Model for vision processing |
| `screenshot_enabled` | Auto-capture screen for vision models |
| `use_advanced_vision` | Multi-model vision pipeline |

#### Context-Aware Features (ComoRAG)

| Property | Description |
|----------|-------------|
| `use_comorag` | Enable context-aware reasoning |
| `max_cycles` | Maximum reasoning cycles |
| `memory_size` | Context retention size |

#### Quick Templates

LLM nodes include templates for common tasks:

| Template | Description |
|----------|-------------|
| Information Extraction | OCR + LLM for data extraction |
| Form Filling | Vision + LLM for form completion |
| Data Processing Chain | Transform and process data |
| Text Analysis | Analyze and categorize text |
| Content Summarization | Summarize content |
| Tool Selection | LLM-powered decision making |
| Python Code Generation | Generate Python code |

**Color**: Orange (#FF6B35)
**Ports**: 1 input, 1 output

<!-- IMAGE PLACEHOLDER: LLM node configuration dialog -->

### 4. TTS Node

**Purpose**: Speaks text aloud using text-to-speech.

| Property | Description |
|----------|-------------|
| `text` | Text to speak |
| `language` | Language code (en, es, fr, de, etc.) |

**Color**: Cyan (#00CED1)
**Ports**: 1 input, 1 output

### 5. Chain Import Node

**Purpose**: Embeds another chain as a subgraph, enabling workflow composition and reuse.

| Property | Description |
|----------|-------------|
| `chain_file` | Path to the chain JSON to import |
| `import_mode` | full, sequences_only, or nodes_only |
| `prefix` | Prefix for imported node names |
| `loop_count` | Times to execute the imported chain |
| `extra_delay` | Delay between loop iterations |

**Color**: Green (#32CD32)
**Ports**: 1 input, 1 output

### 6. Code Node

**Purpose**: Executes custom Python code during workflow execution.

| Property | Description |
|----------|-------------|
| `code` | Python code to execute |
| `source_type` | "inline" or "file" |
| `file_path` | Path to Python file (if file source) |
| `timeout` | Maximum execution time (seconds) |
| `output_variable` | Variable name for result |

**Available Variables**:
- `input_data` - Data from previous node
- `args` - Additional arguments
- `result` - Assign your output to this variable

**Color**: Yellow (#F1C40F)
**Ports**: 1 input, 1 output

### 7. Form Filler Node

**Purpose**: Automatically fills form fields using AI understanding.

*Under Development - Not yet accessible to users*

### 8. Container (VM) Node

*Not yet accessible to users - Under Development*

---

## Recording Special Actions

### Modifier Key Combinations

| Action | Recording Method | Result |
|--------|-----------------|--------|
| **Ctrl+Click** | Hold Ctrl + Left Click | Recorded as `ctrl_click` type |
| **Shift+Click** | Hold Shift + Left Click | Recorded as `shift_click` type |
| **Absolute Click** | Hold Right-Shift + Left Click | Recorded as `absolute_click` (ignores offsets) |

### Clipboard Operations

| Shortcut | Recorded As |
|----------|------------|
| Ctrl+C (Copy) | `clipboard` with operation "c" |
| Ctrl+V (Paste) | `clipboard` with operation "v" |
| Ctrl+X (Cut) | `clipboard` with operation "x" |
| Ctrl+A (Select All) | `clipboard` with operation "a" |
| Ctrl+Z (Undo) | `clipboard` with operation "z" |
| Ctrl+Y (Redo) | `clipboard` with operation "y" |

### Mouse Drag Recording

1. Press and hold left mouse button at start position
2. Drag to end position
3. Release

**Result**: Recorded as `drag_drop` action with `from` and `to` coordinates.

### Right-Ctrl Mouse Movement

Holding **Right-Ctrl** while moving the mouse records a `move_to` action. This is useful for positioning the cursor without performing a click.

---

## Node Dialogs Reference

Each node type has a configuration dialog accessible via double-click or context menu.

### Sequence Node Dialog

<!-- IMAGE PLACEHOLDER: Sequence properties dialog -->

| Field | Effect on Behavior |
|-------|-------------------|
| **Loop Count** | Number of times the sequence repeats. Higher values = more repetitions. |
| **Extra Delay** | Wait time (seconds) added between each loop iteration. Use for rate limiting or waiting for UI updates. |
| **Use App Opened** | When enabled, the player will attempt to focus and activate the application that was active when the sequence was recorded before playback begins. |

### Conditional Node Dialog

<!-- IMAGE PLACEHOLDER: Advanced conditional dialog -->

The conditional dialog is organized into sections:

#### Code Condition Section

| Field | Effect on Behavior |
|-------|-------------------|
| **Enable** | Toggle code condition evaluation |
| **Code** | Python code that must return True/False. Access input via `input_data` variable. |
| **Timeout (s)** | Maximum time allowed for code execution |

#### Presence Triggers Section

| Field | Effect on Behavior |
|-------|-------------------|
| **Add Presence Trigger** | Add an image that must be visible for the condition to pass |
| **Confidence** | Match threshold (0.1-1.0, higher = stricter) |
| **Timeout** | Maximum wait time; if 0, waits indefinitely |

**Note**: To check for absence, connect nodes to the False output of the Presence Trigger. The True output fires when the image is found, the False output fires when the image is not found (or timeout occurs).

#### Layout Match Section

| Field | Effect on Behavior |
|-------|-------------------|
| **Add Layout Match** | Add an image + position requirement |
| **Position Tolerance** | Pixel deviation allowed from captured position |
| **Timeout** | Maximum wait time |

#### OCR Triggers Section

| Field | Effect on Behavior |
|-------|-------------------|
| **Add OCR Trigger** | Add text that must be present |
| **Target Text** | Text to search for on screen |
| **Confidence** | OCR confidence threshold |

#### Pre-Sequence Conditional Loops Section

| Field | Effect on Behavior |
|-------|-------------------|
| **Add Pre-Loop** | Add a loop that runs before the main sequence |
| **Loop Type** | while_present, while_absent, until_present, until_absent |
| **Sequence File** | Path to sequence executed each iteration |
| **Max Iterations** | Maximum loop count |
| **Iteration Delay** | Wait between iterations |

### LLM Node Dialog

<!-- IMAGE PLACEHOLDER: LLM configuration dialog -->

#### Basic Tab

| Field | Effect on Behavior |
|-------|-------------------|
| **Model** | Ollama model for inference |
| **Temperature** | Response randomness (0=deterministic, 2=random) |
| **Max Tokens** | Maximum response length |
| **Output Variable** | Variable name to store response |

#### Prompt Tab

| Field | Effect on Behavior |
|-------|-------------------|
| **System Message** | Instructions defining AI behavior |
| **Prompt** | User query or template |
| **Input Source** | None, OCR, Previous Node, or Clipboard |
| **OCR Confidence** | Threshold for text extraction |
| **Text Preprocessing** | How to process extracted text |

#### Advanced Tab

| Field | Effect on Behavior |
|-------|-------------------|
| **Write Text** | Auto-type generated text output |
| **Typing Batch Size** | Characters per typing batch |
| **Typing Batch Delay** | Delay between batches |
| **Enable Async** | Process requests asynchronously |

#### Features Tab

| Field | Effect on Behavior |
|-------|-------------------|
| **Use Vision** | Enable image understanding |
| **Vision Model** | Model for processing images |
| **Screenshot** | Auto-capture screen for vision |
| **Use Advanced Vision** | Multi-model vision pipeline |
| **Enable ComoRAG** | Context-aware reasoning |
| **Max Cycles** | Reasoning depth |
| **Memory Size** | Context retention |

#### Templates Tab

Pre-configured templates for common LLM use cases. Selecting a template auto-fills model, prompt, and relevant options.

### Chain Import Dialog

<!-- IMAGE PLACEHOLDER: Chain import dialog -->

| Field | Effect on Behavior |
|-------|-------------------|
| **Chain File** | Path to the chain JSON to embed |
| **Import Mode** | full = all nodes, sequences_only = only sequence nodes, nodes_only = only logic nodes |
| **Node Prefix** | Added to all imported node names to prevent conflicts |
| **Loop Count** | Repetitions for the imported chain |
| **Extra Delay** | Wait between loop iterations |
| **Run in Sandbox** | Execute inside a private RDP session |
| **Show Sandbox Window** | Display session for debugging |

### Code Node Dialog

<!-- IMAGE PLACEHOLDER: Code node dialog -->

| Tab | Field | Effect on Behavior |
|-----|-------|-------------------|
| **AI Generator** | Model | Ollama model for code generation |
| | Prompt | Description of desired code functionality |
| **Editor** | Code | Python source code to execute |
| **Settings** | Source Type | "inline" for typed code, "file" for external .py file |
| | File Path | Location of Python file (if source_type is file) |
| | Timeout | Maximum execution time in seconds |
| | Output Variable | Variable name for storing execution result |

### TTS Node Dialog

<!-- IMAGE PLACEHOLDER: TTS dialog -->

| Field | Effect on Behavior |
|-------|-------------------|
| **Text** | Content to be spoken aloud |
| **Language** | BCP-47 language code for speech synthesis |

---

## Sandboxing

Sandboxing in Looper uses **RDPWrap** to give the automation agent its own private Windows session, allowing background task execution without interfering with the user's active desktop.

### How RDP Sandbox Works

1. **RDPWrap Installation**: Enables multiple concurrent RDP sessions on Windows
2. **Agent User**: Creates a dedicated user account (`LooperAgent`) for the agent
3. **Private Session**: The agent runs in a separate RDP session that doesn't affect the user's active desktop
4. **Communication**: Looper communicates with the agent via HTTP API

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     User's Desktop                          │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   Looper    │───▶│ Agent Bridge │───▶│ HTTP API      │  │
│  │   GUI       │◀───│  (relay)    │◀───│ (localhost)   │  │
│  └─────────────┘    └──────────────┘    └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ RDP Session Communication
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              LooperAgent Private RDP Session                 │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   Agent     │◀───│ Screen       │───▶│  Application  │  │
│  │  Handler    │───▶│ Capture      │    │  Under Test   │  │
│  └─────────────┘    └──────────────┘    └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Agent Actions

The sandbox agent supports these actions via HTTP POST:

| Action | Parameters | Description |
|--------|------------|-------------|
| `click` | x, y, button | Move to (x,y) and click |
| `type` | text | Type text string |
| `hotkey` | keys[] | Press key combination |
| `screenshot` | (none) | Capture and return screen as base64 PNG |
| `scroll` | amount | Scroll by amount (negative = down) |
| `drag_start` | x, y | Start drag at coordinates |
| `drag_end` | x, y | End drag at coordinates |
| `drag_drop` | start_x, start_y, end_x, end_y | Complete drag operation |

### Setting Up Sandbox

1. Run Looper from `run_looper.bat` which sets up the environment
2. RDPWrap is installed automatically if not present
3. The `LooperAgent` user is created with necessary permissions
4. Registry is configured for alternate shell support

### Sandbox vs Direct Execution

| Aspect | Direct Execution | RDP Sandbox |
|--------|-----------------|-------------|
| Isolation | None - runs on host | Full isolation in separate RDP session |
| User Interference | Can interrupt user's work | Agent works independently |
| Speed | Faster | Slower (session overhead) |
| Use Case | Trusted, quick tasks | Background agents, long-running tasks |

### Debugging Sandbox Issues

If sandbox fails to start:

1. Ensure RDPWrap is installed: Run `run_looper.bat`
2. Check that `LooperAgent` user exists
3. Verify the user is in Remote Desktop Users and Administrators groups
4. Check `agent_info.json` for connection details

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Ctrl+C** | Copy selected nodes |
| **Ctrl+V** | Paste copied nodes |
| **ESC** | Stop recording / Cancel execution |

---

## File Formats

### Sequence JSON

```json
{
  "metadata": {
    "created_at": "2025-01-15 10:30:00",
    "total_actions": 25,
    "duration_sec": 45.2,
    "mode": "desktop_only"
  },
  "actions": [
    {
      "type": "click",
      "button": "left",
      "coordinates": {"x": 500, "y": 300},
      "screenshot": "screenshots/click_001.png",
      "timestamp": 1705312200.123
    }
  ]
}
```

### Chain JSON

```json
{
  "sequences": [
    {
      "sequence_file": "login_sequence.json",
      "loop_count": 1,
      "extra_delay": 0.5
    }
  ],
  "conditional_nodes": [...],
  "llm_nodes": [...],
  "tts_nodes": [...]
}
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Sequence plays wrong app | Enable "Use App Opened" or manually focus target app |
| Click misses target | Adjust confidence threshold lower (0.7-0.8) |
| OCR not finding text | Increase confidence threshold, check language setting |
| Scheduler not running | Check if Looper is running, verify schedules.json exists |
| Sandbox fails | Ensure rdpwrap is installed and run the app from run_looper.bat to set up |
| Conditional loop not ending | Check max_iterations limit and loop condition |

---

*Documentation version 1.0 - Last updated for Looper Release 1.5.0*
