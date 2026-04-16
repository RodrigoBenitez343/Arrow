# Sequence Management

<cite>
**Referenced Files in This Document**
- [sequence_manager.py](file://LoOper/recorder/sequence_manager.py)
- [element_recorder.py](file://LoOper/recorder/element_recorder.py)
- [screenshot_manager.py](file://LoOper/recorder/screenshot_manager.py)
- [scroll_manager.py](file://LoOper/recorder/scroll_manager.py)
- [keyboard_handler.py](file://LoOper/recorder/keyboard_handler.py)
- [mouse_handler.py](file://LoOper/recorder/mouse_handler.py)
- [sequence_player.py](file://LoOper/player/sequence_player.py)
- [json_cache.py](file://LoOper/player/json_cache.py)
- [sequence_executor.py](file://LoOper/player/multi_sequence/sequence_executor.py)
- [action_handlers.py](file://LoOper/player/action_handlers.py)
- [base_bot.py](file://LoOper/player/base_bot.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the sequence management subsystem responsible for capturing, organizing, serializing, and playing back user interactions. It covers:
- Smart action grouping strategies that merge related mouse and keyboard events into efficient sequences
- Chronological organization and action-type handling
- Playback optimization via caching and fast JSON loading
- Coordination between the element recorder and sequence manager during capture
- Integration with screenshot management and template-based recognition
- Practical guidance on sequence integrity, memory management, and performance for long recordings

## Project Structure
The sequence management subsystem spans two primary areas:
- Recorder: captures user interactions and builds action sequences
- Player: loads, validates, and executes sequences with performance optimizations

```mermaid
graph TB
subgraph "Recorder"
ER["ElementRecorder"]
SM["SequenceManager"]
KM["KeyboardHandler"]
MM["MouseHandler"]
SCRM["ScrollManager"]
SSM["ScreenshotManager"]
end
subgraph "Player"
SP["SequencePlayer"]
JC["JSONCache"]
SE["SequenceExecutor"]
AH["ActionHandlers"]
BB["BaseBot"]
end
ER --> KM
ER --> MM
ER --> SCRM
ER --> SSM
ER --> SM
SM --> SP
SP --> JC
SP --> AH
AH --> BB
SE --> SP
```

**Diagram sources**
- [element_recorder.py:11-25](file://LoOper/recorder/element_recorder.py#L11-L25)
- [sequence_manager.py:18-24](file://LoOper/recorder/sequence_manager.py#L18-L24)
- [keyboard_handler.py:6-18](file://LoOper/recorder/keyboard_handler.py#L6-L18)
- [mouse_handler.py:6-17](file://LoOper/recorder/mouse_handler.py#L6-L17)
- [scroll_manager.py:4-12](file://LoOper/recorder/scroll_manager.py#L4-L12)
- [screenshot_manager.py:10-19](file://LoOper/recorder/screenshot_manager.py#L10-L19)
- [sequence_player.py:136-161](file://LoOper/player/sequence_player.py#L136-L161)
- [json_cache.py:15-28](file://LoOper/player/json_cache.py#L15-L28)
- [sequence_executor.py:17-25](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L25)
- [action_handlers.py:242-270](file://LoOper/player/action_handlers.py#L242-L270)
- [base_bot.py:242-270](file://LoOper/player/base_bot.py#L242-L270)

**Section sources**
- [element_recorder.py:11-25](file://LoOper/recorder/element_recorder.py#L11-L25)
- [sequence_manager.py:18-24](file://LoOper/recorder/sequence_manager.py#L18-L24)
- [sequence_player.py:136-161](file://LoOper/player/sequence_player.py#L136-L161)
- [json_cache.py:15-28](file://LoOper/player/json_cache.py#L15-L28)

## Core Components
- SequenceManager: central coordinator for appending actions, attaching app context, and persisting sequences to JSON with metadata.
- ElementRecorder: orchestrates capture of mouse, keyboard, scroll, and screenshot events, then forwards actions to SequenceManager.
- ScrollManager: aggregates scroll deltas into cohesive scroll bursts with timing and direction.
- KeyboardHandler: tracks modifiers and converts keystrokes, clipboard shortcuts, and typed strings into normalized actions.
- MouseHandler: detects clicks, double-clicks, and drags; emits standardized action dictionaries.
- ScreenshotManager: captures small regions around click coordinates for visual context.
- SequencePlayer: loads and executes sequences with caching and optimized JSON parsing.
- JSONCache: centralized cache for chains and sequences to avoid repeated IO and parsing.
- SequenceExecutor: resolves expected app context and ensures target applications are ready before playback.
- ActionHandlers and BaseBot: dispatch actions to platform-specific handlers and bots.

**Section sources**
- [sequence_manager.py:18-178](file://LoOper/recorder/sequence_manager.py#L18-L178)
- [element_recorder.py:11-155](file://LoOper/recorder/element_recorder.py#L11-L155)
- [scroll_manager.py:4-97](file://LoOper/recorder/scroll_manager.py#L4-L97)
- [keyboard_handler.py:6-197](file://LoOper/recorder/keyboard_handler.py#L6-L197)
- [mouse_handler.py:6-167](file://LoOper/recorder/mouse_handler.py#L6-L167)
- [screenshot_manager.py:10-49](file://LoOper/recorder/screenshot_manager.py#L10-L49)
- [sequence_player.py:136-252](file://LoOper/player/sequence_player.py#L136-L252)
- [json_cache.py:15-584](file://LoOper/player/json_cache.py#L15-L584)
- [sequence_executor.py:17-121](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L121)
- [action_handlers.py:242-270](file://LoOper/player/action_handlers.py#L242-L270)
- [base_bot.py:242-270](file://LoOper/player/base_bot.py#L242-L270)

## Architecture Overview
The recorder captures user interactions and builds a chronological sequence of actions. The player loads and executes these actions efficiently, leveraging caches and optimized IO.

```mermaid
sequenceDiagram
participant User as "User"
participant ER as "ElementRecorder"
participant KM as "KeyboardHandler"
participant MM as "MouseHandler"
participant SCRM as "ScrollManager"
participant SSM as "ScreenshotManager"
participant SM as "SequenceManager"
participant SP as "SequencePlayer"
participant JC as "JSONCache"
participant AH as "ActionHandlers"
participant BB as "BaseBot"
User->>MM : Mouse move/click/drag
User->>KM : Keystrokes/modifiers
User->>SCRM : Scroll events
MM-->>ER : Mouse action
KM-->>ER : Keypress/flush
SCRM-->>ER : Scroll burst finalized
ER->>SSM : Capture click screenshot (when needed)
ER->>SM : add_action(action)
SM->>SM : attach app_context
SM-->>SP : save_sequence(filename)
SP->>JC : get_sequence()/preload_chain()
SP->>AH : execute_with_timing(action)
AH->>BB : dispatch to bot handlers
BB-->>AH : completion
AH-->>SP : success/failure
```

**Diagram sources**
- [element_recorder.py:32-155](file://LoOper/recorder/element_recorder.py#L32-L155)
- [keyboard_handler.py:29-197](file://LoOper/recorder/keyboard_handler.py#L29-L197)
- [mouse_handler.py:25-167](file://LoOper/recorder/mouse_handler.py#L25-L167)
- [scroll_manager.py:13-97](file://LoOper/recorder/scroll_manager.py#L13-L97)
- [screenshot_manager.py:21-49](file://LoOper/recorder/screenshot_manager.py#L21-L49)
- [sequence_manager.py:125-178](file://LoOper/recorder/sequence_manager.py#L125-L178)
- [sequence_player.py:162-252](file://LoOper/player/sequence_player.py#L162-L252)
- [json_cache.py:163-212](file://LoOper/player/json_cache.py#L163-L212)
- [action_handlers.py:242-270](file://LoOper/player/action_handlers.py#L242-L270)
- [base_bot.py:242-270](file://LoOper/player/base_bot.py#L242-L270)

## Detailed Component Analysis

### SequenceManager: Action Collection, App Context, and Serialization
- Chronological ordering: actions are appended in the order received.
- App context attachment: enriches actions with active window/process metadata when available.
- Serialization: writes a JSON structure with metadata and action list; clears caches post-save to keep chains synchronized.

```mermaid
classDiagram
class SequenceManager {
+recorded_actions : list
+start_time : float
+add_action(action)
+save_sequence(filename)
+get_action_count() int
+clear_actions() void
-_get_active_app_context() dict
-_attach_app_context(action) dict
}
```

**Diagram sources**
- [sequence_manager.py:18-178](file://LoOper/recorder/sequence_manager.py#L18-L178)

**Section sources**
- [sequence_manager.py:18-178](file://LoOper/recorder/sequence_manager.py#L18-L178)

### ElementRecorder: Coordinated Capture and Action Dispatch
- Orchestrates handlers and screenshot manager, then forwards actions to SequenceManager.
- Specialized behaviors:
  - Clicks: supports absolute click, ctrl-click, shift-click variants based on modifier keys.
  - Scroll: delegates to ScrollManager to finalize bursts.
  - Keyboard: flushes typed strings and records clipboard shortcuts as separate actions.
  - Mouse move with right Ctrl: captures start/end positions to emit a move_to action on release.

```mermaid
sequenceDiagram
participant ER as "ElementRecorder"
participant MM as "MouseHandler"
participant KM as "KeyboardHandler"
participant SCRM as "ScrollManager"
participant SSM as "ScreenshotManager"
participant SM as "SequenceManager"
ER->>MM : handle_mouse_release(x,y,button)
MM-->>ER : click/double-click/drag action
ER->>SSM : capture_click_screenshot(...) (if needed)
ER->>SM : add_action(click/absolute/ctrl/shift)
ER->>KM : handle_keypress(key)
KM-->>ER : type_string/keystroke/clipboard actions
ER->>SM : add_action(...)
ER->>SCRM : process_scroll(x,y,dx,dy)
SCRM-->>ER : finalized scroll action (when burst ends)
ER->>SM : add_action(scroll)
```

**Diagram sources**
- [element_recorder.py:32-155](file://LoOper/recorder/element_recorder.py#L32-L155)
- [mouse_handler.py:44-167](file://LoOper/recorder/mouse_handler.py#L44-L167)
- [keyboard_handler.py:29-197](file://LoOper/recorder/keyboard_handler.py#L29-L197)
- [scroll_manager.py:13-97](file://LoOper/recorder/scroll_manager.py#L13-L97)
- [screenshot_manager.py:21-49](file://LoOper/recorder/screenshot_manager.py#L21-L49)
- [sequence_manager.py:125-137](file://LoOper/recorder/sequence_manager.py#L125-L137)

**Section sources**
- [element_recorder.py:11-155](file://LoOper/recorder/element_recorder.py#L11-L155)

### ScrollManager: Burst-Based Scroll Grouping
- Aggregates scroll events within a short timeout and same-direction constraint.
- Emits a single scroll action with cumulative delta, start/end positions, duration, and step count.

```mermaid
flowchart TD
Start(["process_scroll(x,y,dx,dy)"]) --> InitBurst["Initialize or extend burst"]
InitBurst --> TimeoutCheck{"Within timeout<br/>and same direction?"}
TimeoutCheck --> |Yes| ExtendBurst["Update total_delta, end, steps,<br/>final_position, last_time"]
TimeoutCheck --> |No| FinalizePrev["finalize_scroll_burst()"]
FinalizePrev --> NewBurst["Create new burst with current delta"]
ExtendBurst --> ReturnNone["Return None (ongoing)"]
NewBurst --> ReturnPrev["Return previous finalized action (if any)"]
```

**Diagram sources**
- [scroll_manager.py:13-97](file://LoOper/recorder/scroll_manager.py#L13-L97)

**Section sources**
- [scroll_manager.py:4-97](file://LoOper/recorder/scroll_manager.py#L4-L97)

### KeyboardHandler: Modifier-Aware Typing and Clipboard Actions
- Tracks modifier keys and converts Ctrl+C/X/V/A/Z/Y into clipboard actions.
- Buffers typed characters into a string buffer and flushes to a type_string action when needed.
- Records standalone control keys as keystroke actions.

```mermaid
flowchart TD
KP["handle_keypress(key)"] --> ModCheck{"Modifier key?"}
ModCheck --> |Yes| SetMod["Update modifiers dict"] --> ReturnNone["Return None"]
ModCheck --> |No| CtrlCombo{"Ctrl+letter?"}
CtrlCombo --> |Yes| FlushStr["flush_current_string()"] --> MakeClipboard["Create clipboard action"] --> ReturnBoth["Return [string_action, clipboard]"]
CtrlCombo --> |No| Printable{"Printable char?"}
Printable --> |Yes| BufferChar["Append to current_string"] --> ReturnNone
Printable --> |No| FlushStr2["flush_current_string()"] --> MakeKeystroke["Create keystroke action"] --> ReturnBoth2["Return [string_action, keystroke] or keystroke"]
```

**Diagram sources**
- [keyboard_handler.py:29-197](file://LoOper/recorder/keyboard_handler.py#L29-L197)

**Section sources**
- [keyboard_handler.py:6-197](file://LoOper/recorder/keyboard_handler.py#L6-L197)

### MouseHandler: Click, Double-Click, and Drag Detection
- Detects double-clicks within a time and spatial threshold.
- Treats small movements as clicks; larger movements as drag_drop.
- Emits click actions with a needs_screenshot flag for later capture.

```mermaid
flowchart TD
Release["handle_mouse_release(x,y,button)"] --> LeftBtn{"Left button?"}
LeftBtn --> |Yes| Distance["Compute distance from drag_start"]
Distance --> Moved{"Distance > threshold?"}
Moved --> |Yes| Drag["Create drag_drop action"] --> ReturnDrag["Return action"]
Moved --> |No| DoubleCheck["Check double-click conditions"]
LeftBtn --> |No| OtherBtn["Other button"] --> SingleClick["Create click action with needs_screenshot"] --> ReturnClick["Return action"]
DoubleCheck --> Double{"Double-click?"}
Double --> |Yes| DoubleAct["Create double_click with needs_screenshot"] --> ReturnDouble["Return action"]
Double --> |No| SingleAct["Create click action with needs_screenshot"] --> ReturnSingle["Return action"]
```

**Diagram sources**
- [mouse_handler.py:44-167](file://LoOper/recorder/mouse_handler.py#L44-L167)

**Section sources**
- [mouse_handler.py:6-167](file://LoOper/recorder/mouse_handler.py#L6-L167)

### ScreenshotManager: Visual Context for Clicks
- Captures a small square region around click coordinates and saves a PNG file.
- Returns the saved path for inclusion in click actions.

```mermaid
flowchart TD
Capture["capture_click_screenshot(x,y,region_size)"] --> Calc["Compute left/top bounds"]
Calc --> Take["Take screenshot(region)"]
Take --> Save["Increment counter, build filename,<br/>save to screenshots_dir"]
Save --> ReturnPath["Return path or None"]
```

**Diagram sources**
- [screenshot_manager.py:21-49](file://LoOper/recorder/screenshot_manager.py#L21-L49)

**Section sources**
- [screenshot_manager.py:10-49](file://LoOper/recorder/screenshot_manager.py#L10-L49)

### SequencePlayer: Playback with Caching and Fast JSON Loading
- Uses a thread-safe SequenceCache to avoid repeated file IO and parsing.
- Implements optimized JSON loading with buffer sizing and streaming for large files.
- Supports lazy loading and validates sequence structure before playback.

```mermaid
classDiagram
class SequenceCache {
-_cache : dict
-_file_timestamps : dict
-_max_size : int
-_lock : Lock
+get(filename) dict
+put(filename, data) void
+clear() void
+size() int
}
class SequencePlayer {
-sequence_file : str
-sequence_data : dict
-running : bool
-loop_counter : int
-last_app_context : dict
+load_sequence(filename) dict
+play_sequence(fallback_callback, stop_flag, skip_initial_delay) void
-_ensure_sequence_loaded() void
}
SequencePlayer --> SequenceCache : "uses"
```

**Diagram sources**
- [sequence_player.py:14-92](file://LoOper/player/sequence_player.py#L14-L92)
- [sequence_player.py:136-252](file://LoOper/player/sequence_player.py#L136-L252)

**Section sources**
- [sequence_player.py:14-92](file://LoOper/player/sequence_player.py#L14-L92)
- [sequence_player.py:136-252](file://LoOper/player/sequence_player.py#L136-L252)

### JSONCache: Centralized Chain and Sequence Caching
- Preloads chains and all referenced sequences to minimize runtime IO.
- Provides path resolution strategies for flexible sequence locations.
- Supports invalidation and dependency tracking to keep caches consistent.

```mermaid
classDiagram
class JSONCache {
-_sequence_cache : dict
-_chain_cache : dict
-_path_cache : dict
-_loaded_chains : set
-_sequence_mtimes : dict
-_chain_mtimes : dict
+preload_chain(chain_file_path, chain_file_dir) dict
+get_sequence(sequence_file, base_dir) dict
+get_chain(chain_file_path) dict
+invalidate_file(file_path) void
+invalidate_directory(directory_path) void
+clear_cache() void
+get_cache_stats() dict
}
```

**Diagram sources**
- [json_cache.py:15-584](file://LoOper/player/json_cache.py#L15-L584)

**Section sources**
- [json_cache.py:15-584](file://LoOper/player/json_cache.py#L15-L584)

### SequenceExecutor: App Context Awareness and Foreground Management
- Extracts expected app context from actions and ensures the target application is in the foreground.
- Handles sandboxed execution and RDP agent scenarios for reliable playback.

```mermaid
flowchart TD
Start(["begin_chain_sandbox/app context"]) --> Extract["Extract app_context from actions"]
Extract --> Foreground{"Is app in foreground?"}
Foreground --> |Yes| Proceed["Proceed with playback"]
Foreground --> |No| Focus["Attempt to focus/find window"]
Focus --> Launched{"Launched or reachable?"}
Launched --> |Yes| Proceed
Launched --> |No| Fail["Return False"]
```

**Diagram sources**
- [sequence_executor.py:26-121](file://LoOper/player/multi_sequence/sequence_executor.py#L26-L121)

**Section sources**
- [sequence_executor.py:17-121](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L121)

### Action Dispatch and Execution
- BaseBot routes actions to specialized handlers based on type.
- ActionHandlers implement platform-specific execution (clicks, scrolls, keystrokes, clipboard, drag).

```mermaid
sequenceDiagram
participant SP as "SequencePlayer"
participant AH as "ActionHandlers"
participant BB as "BaseBot"
SP->>AH : execute_with_timing(idx, action, ...)
AH->>BB : dispatch to bot handler (click/scroll/type/etc.)
BB-->>AH : result
AH-->>SP : success/fallback
```

**Diagram sources**
- [base_bot.py:242-270](file://LoOper/player/base_bot.py#L242-L270)
- [action_handlers.py:538-561](file://LoOper/player/action_handlers.py#L538-L561)

**Section sources**
- [base_bot.py:242-270](file://LoOper/player/base_bot.py#L242-L270)
- [action_handlers.py:538-561](file://LoOper/player/action_handlers.py#L538-L561)

## Dependency Analysis
- ElementRecorder depends on all handlers and SequenceManager; it also coordinates ScreenshotManager for click actions.
- SequenceManager depends on JSONCache for cache invalidation after saving sequences.
- SequencePlayer depends on JSONCache for fast retrieval and on ActionHandlers/BaseBot for execution.
- SequenceExecutor depends on app context extraction and OS/windowing APIs to ensure proper foreground state.

```mermaid
graph LR
ER["ElementRecorder"] --> KM["KeyboardHandler"]
ER --> MM["MouseHandler"]
ER --> SCRM["ScrollManager"]
ER --> SSM["ScreenshotManager"]
ER --> SM["SequenceManager"]
SM --> JC["JSONCache"]
SP["SequencePlayer"] --> JC
SP --> AH["ActionHandlers"]
AH --> BB["BaseBot"]
SE["SequenceExecutor"] --> SP
```

**Diagram sources**
- [element_recorder.py:14-21](file://LoOper/recorder/element_recorder.py#L14-L21)
- [sequence_manager.py:8-16](file://LoOper/recorder/sequence_manager.py#L8-L16)
- [sequence_player.py:12-14](file://LoOper/player/sequence_player.py#L12-L14)
- [sequence_executor.py:17-25](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L25)

**Section sources**
- [element_recorder.py:14-21](file://LoOper/recorder/element_recorder.py#L14-L21)
- [sequence_manager.py:8-16](file://LoOper/recorder/sequence_manager.py#L8-L16)
- [sequence_player.py:12-14](file://LoOper/player/sequence_player.py#L12-L14)
- [sequence_executor.py:17-25](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L25)

## Performance Considerations
- Use SequenceCache and JSONCache to avoid repeated file IO and parsing.
- Prefer lazy loading in SequencePlayer when playback is deferred.
- Optimize JSON loading with buffer sizing and streaming for large sequences.
- Keep screenshot capture selective (only for clicks requiring visual context) to reduce IO overhead.
- Minimize cache churn by invalidating only affected files rather than clearing entire caches.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- Sequence integrity
  - Ensure actions include required fields (e.g., actions array) before playback.
  - Verify app_context presence when targeting specific applications.
- Memory management
  - Monitor cache sizes and clear caches when rebuilding chains or sequences.
  - Use JSONCache invalidation for modified files to avoid stale data.
- Long recording sessions
  - Limit screenshot frequency to reduce disk IO.
  - Use lazy loading and caching to keep playback responsive.
- Playback reliability
  - Confirm the target application is in the foreground or use SequenceExecutor’s foreground management.
  - For sandboxed environments, ensure agent reachability and URLs are valid.

**Section sources**
- [sequence_player.py:162-193](file://LoOper/player/sequence_player.py#L162-L193)
- [json_cache.py:404-448](file://LoOper/player/json_cache.py#L404-L448)
- [sequence_executor.py:122-777](file://LoOper/player/multi_sequence/sequence_executor.py#L122-L777)

## Conclusion
The sequence management subsystem integrates capture, serialization, and playback with thoughtful grouping strategies and performance optimizations. By coordinating handlers, managing app context, and leveraging caching, it enables robust, efficient automation workflows. For long sessions and complex chains, rely on the provided caching mechanisms and careful invalidation to maintain integrity and responsiveness.