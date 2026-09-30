# Modularized Recorder

This directory contains the modularized version of the recorder script, broken down into logical components for better maintainability.

## Module Structure

### Core Modules

1. **`element_recorder.py`** - Main recorder class that coordinates all functionality
2. **`sequence_manager.py`** - Manages action recording and saving to JSON
3. **`screenshot_manager.py`** - Handles screenshot capture for click actions
4. **`scroll_manager.py`** - Manages scroll burst tracking and finalization
5. **`keyboard_handler.py`** - Handles keyboard events and modifier key tracking
6. **`mouse_handler.py`** - Handles mouse events including clicks and drag operations
7. **`main.py`** - Entry point that sets up listeners and starts recording

## Usage

### Direct Usage
```python
from modularized.main import start_recording
start_recording()
```

### Using Individual Components
```python
from modularized.element_recorder import ElementRecorder

recorder = ElementRecorder()
# Use recorder methods as needed
```

## Module Responsibilities

### ElementRecorder
- Coordinates all other modules
- Provides the main interface for recording actions
- Handles the integration between different components

### SequenceManager
- Stores recorded actions
- Manages metadata (timestamps, duration, etc.)
- Saves sequences to JSON files
- Provides action count and clearing functionality

### ScreenshotManager
- Captures screenshots around click coordinates
- Manages screenshot directory creation
- Handles screenshot naming and saving
- Provides error handling for screenshot operations

### ScrollManager
- Tracks scroll bursts (multiple scroll events in sequence)
- Manages scroll timing and direction
- Finalizes scroll bursts into single actions
- Handles cumulative scroll position tracking

### KeyboardHandler
- Processes keyboard press and release events
- Manages modifier key states (Ctrl, Shift, Alt)
- Handles special key combinations (Ctrl+C, Ctrl+V, etc.)
- Manages string buffering for typed text

### MouseHandler
- Processes mouse press and release events
- Manages drag detection and tracking
- Distinguishes between clicks and drags
- Handles different mouse buttons

## Benefits of Modularization

1. **Maintainability** - Each module has a single responsibility
2. **Testability** - Individual components can be tested in isolation
3. **Reusability** - Modules can be used independently or in different combinations
4. **Readability** - Code is organized logically and easier to understand
5. **Extensibility** - New features can be added to specific modules without affecting others

## Configuration

Each module can be configured independently:

- **ScreenshotManager**: Screenshot directory, region size
- **ScrollManager**: Scroll timeout, epsilon values
- **KeyboardHandler**: Modifier key mappings, special combos (Ctrl+C/V/X/A, Ctrl+Z/Y)
- **MouseHandler**: Drag distance threshold
- **SequenceManager**: Output filename, metadata format