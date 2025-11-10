# LoOper - Advanced Desktop Automation Platform

LoOper is a powerful, modular desktop automation platform that allows you to record, edit, and replay complex sequences of mouse clicks, keyboard inputs, scrolling, and drag-and-drop operations with advanced visual template matching capabilities.

## 🌟 Overview

LoOper consists of three main components working together to provide a complete automation solution:

- **🎨 NGUI** - Visual node-based GUI for creating and managing automation workflows
- **📹 Recorder** - Modular system for recording user interactions
- **▶️ Player** - Sophisticated player with AI integration and computer vision capabilities

## 📁 Project Structure

```
LoOper/
├── main.py                 # Main application entry point
├── requirements.txt        # Python dependencies
├── NGUI/                  # Graphical User Interface
│   ├── app.py             # GUI application launcher
│   ├── main_window.py     # Main application window
│   ├── graph_view.py      # Node graph visualization
│   ├── nodes.py           # Node type definitions
│   ├── constants.py       # UI constants and themes
│   ├── graph_elements/    # Modular UI components
│   └── dialogs/           # Configuration dialogs
├── recorder/              # Recording module
│   ├── main.py            # Recording entry point
│   ├── element_recorder.py # Main recorder coordinator
│   ├── sequence_manager.py # Action sequence management
│   ├── screenshot_manager.py # Screenshot handling
│   ├── scroll_manager.py  # Scroll action management
│   ├── keyboard_handler.py # Keyboard event processing
│   └── mouse_handler.py   # Mouse event processing
├── player/               # Playback module
│   ├── main.py           # Player entry point
│   ├── sequence_player.py # Single sequence player
│   ├── multi_sequence_player.py # Chain execution
│   ├── base_bot.py       # Core automation functionality
│   ├── action_handlers.py # Action execution handlers
│   ├── computer_vision.py # Template matching
│   ├── config.py         # Configuration and logging
│   └── multi_sequence/   # Advanced workflow components
└── AI/                   # AI integration
    ├── consult.py        # LLM client integration
    └── model_cache.py    # Model management
```

## 🚀 Installation

### Prerequisites
- Python 3.7 or higher
- Windows (primary support)

### Dependencies Installation
```bash
pip install -r requirements.txt
```

### Key Dependencies
- **PyQt5** - GUI framework
- **NodeGraphQt** - Node-based graph visualization
- **pynput** - Cross-platform input capture and control
- **PyAutoGUI** - Screen automation
- **opencv-python** - Computer vision and template matching
- **numpy** - Numerical operations
- **Pillow** - Image processing
- **pytesseract** - OCR functionality
- **requests** - HTTP client for API integration

## 🎨 NGUI - Visual Interface

The NGUI (Node-based Graphical User Interface) provides an intuitive visual environment for creating complex automation workflows.

### Core Features

#### 🔗 Node-Based Workflow Design
- **Sequence Nodes**: Represent collections of actions that execute in order
- **Action Nodes**: Individual automation actions (click, type, scroll, etc.)
- **Conditional Nodes**: Control flow based on screen conditions
- **LLM Nodes**: AI-powered decision making and text generation

#### 🎯 Node Types

##### Sequence Nodes
- Group related actions together
- Configure loops and repetition
- Set execution parameters
- Chain multiple sequences

##### Action Nodes
- **Click Actions**: Left/right/double clicks with visual template matching
- **Keyboard Actions**: Text typing, key combinations, special keys
- **Scroll Actions**: Directional scrolling with customizable amounts
- **Delay Actions**: Timed pauses between operations
- **Drag Actions**: Click-and-drag operations
- **Clipboard Actions**: Copy/paste operations

##### Conditional Nodes
- **Image Presence**: Check if specific images appear on screen
- **OCR Text Detection**: Search for text using optical character recognition
- **Time-based Conditions**: Execute based on time constraints
- **Custom Triggers**: User-defined conditional logic

##### LLM Nodes
- **AI Integration**: Connect to Ollama or other LLM services
- **Vision Capabilities**: Analyze screenshots with AI models
- **Dynamic Text Generation**: Generate content based on screen context
- **Decision Making**: AI-powered workflow branching

#### 🖼️ Visual Editor Features

##### Graph View Manager
- **Zoom and Pan**: Navigate large workflows easily
- **Auto-layout**: Automatically arrange nodes for clarity
- **Multi-selection**: Edit multiple nodes simultaneously
- **Connection Management**: Visual connection between nodes

##### UI Components
- **Dark Theme**: Professional dark interface
- **Customizable Layout**: Resizable panels and views
- **Context Menus**: Right-click actions for quick editing
- **Toolbar**: Quick access to common operations

##### Configuration Dialogs
- **Node Properties**: Detailed configuration for each node type
- **Fallback Settings**: Configure alternative actions when primary actions fail
- **Advanced Conditions**: Complex conditional logic setup
- **LLM Configuration**: AI model settings and parameters

### Menu System

#### File Operations
- **New Chain**: Create new automation workflow
- **Open Chain**: Load existing chain files (JSON format)
- **Save Chain**: Save current workflow to file

#### Execution Controls
- **Run Chain**: Execute the entire workflow
- **Record Sequence**: Launch recorder to capture new actions

#### View Management
- **Switch Views**: Toggle between sequence and action views
- **Auto Layout**: Organize nodes automatically
- **Clear Graph**: Reset the workspace

## 📹 Recorder - Action Capture System

The Recorder module captures user interactions with sophisticated precision and intelligence.

### Core Architecture

#### Element Recorder (Main Coordinator)
- Orchestrates all recording components
- Provides unified interface for recording session
- Manages integration between different handlers
- Coordinates screenshot capture with actions

#### Sequence Manager
- **Action Storage**: Maintains sequence of recorded actions
- **Metadata Management**: Tracks timestamps, duration, action count
- **JSON Serialization**: Saves sequences in structured format
- **Session Management**: Handles recording start/stop lifecycle

#### Screenshot Manager
- **Automatic Capture**: Takes screenshots for every click action
- **Region Selection**: Captures specific areas around click points
- **Directory Management**: Organizes screenshots by sequence
- **Error Handling**: Graceful failure recovery for screenshot operations

#### Scroll Manager
- **Burst Detection**: Groups rapid scroll events into single actions
- **Direction Tracking**: Maintains scroll direction consistency
- **Timing Analysis**: Uses timeout to finalize scroll bursts
- **Position Calculation**: Tracks cumulative scroll distances

#### Keyboard Handler
- **Modifier Key Tracking**: Monitors Ctrl, Shift, Alt states
- **Special Combinations**: Recognizes Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+A
- **Text Buffering**: Accumulates typed characters into text actions
- **Key State Management**: Handles press/release event pairs

#### Mouse Handler
- **Click Detection**: Distinguishes clicks from drags
- **Drag Threshold**: Configurable distance for drag detection (>10px)
- **Button Support**: Left, right, and middle mouse buttons
- **Position Tracking**: Records precise screen coordinates

### Recording Features

#### Smart Action Grouping
- **Scroll Bursts**: Multiple scroll events → Single scroll action
- **Text Sequences**: Character sequences → Single type action
- **Modifier Combinations**: Key+modifier → Single keystroke action

#### Visual Template Capture
- **Click Screenshots**: Automatic image capture for visual matching
- **Region-based Capture**: Captures area around click point
- **Organized Storage**: Screenshots saved with descriptive names

#### Real-time Feedback
- **Status Messages**: Live feedback during recording
- **Action Counts**: Track number of recorded actions
- **Error Notifications**: Alert user to recording issues

### Usage Instructions

#### Starting Recording
1. Launch from GUI or run directly
2. Position windows and applications as needed
3. Begin performing actions to record

#### During Recording
- **Normal Actions**: Click, type, scroll as usual
- **Drag Operations**: Hold and drag >10px for drag detection
- **Special Keys**: Ctrl+C/V/X/A are recognized automatically
- **Scroll Groups**: Rapid scrolling is automatically grouped

#### Stopping Recording
- **ESC Key**: Saves sequence and exits recording
- **Automatic Save**: Sequence saved to JSON with screenshots

#### Recorded Data Format
```json
{
  "metadata": {
    "total_duration": 45.67,
    "start_time": "2024-01-15T10:30:00",
    "action_count": 12
  },
  "actions": [
    {
      "type": "click",
      "x": 500, "y": 300,
      "button": "left",
      "screenshot": "screenshots/click_001.png"
    },
    {
      "type": "type",
      "text": "Hello World"
    },
    {
      "type": "scroll",
      "x": 400, "y": 200,
      "dx": 0, "dy": -3
    }
  ]
}
```

## ▶️ Player - Intelligent Automation Engine

The Player module executes recorded sequences with advanced computer vision and AI capabilities.

### Core Architecture

#### Base Bot (SeleniumBot)
- **Human-like Movement**: Natural mouse movement patterns
- **Intelligent Delays**: Variable timing to mimic human behavior
- **Error Recovery**: Robust failure handling and retry logic
- **Action Coordination**: Manages execution of different action types

#### Sequence Player
- **Single Sequence Execution**: Plays individual recorded sequences
- **Action Interpretation**: Converts JSON data to executable actions
- **Visual Matching**: Uses screenshots for precise click targeting
- **Stop Control**: ESC key cancellation during playback

#### Multi-Sequence Player
- **Chain Execution**: Runs multiple sequences in order
- **Loop Control**: Configurable repetition with delays
- **Conditional Branching**: Smart workflow navigation
- **Advanced Fallbacks**: Alternative actions when primary actions fail

#### Computer Vision System
- **Template Matching**: Multi-scale image matching with OpenCV
- **Fallback Mechanisms**: Progressive search strategies
- **Region-based Search**: Targeted image search areas
- **Confidence Scoring**: Match quality assessment

#### Action Handlers
- **Click Handler**: Visual template matching with fallback positions
- **Keyboard Handler**: Text input and key combinations
- **Scroll Handler**: Precise scrolling with direction control
- **Drag Handler**: Complex drag-and-drop operations
- **Clipboard Handler**: Copy/paste operations

### Advanced Features

#### Visual Template Matching
- **Multi-scale Search**: Finds images at different sizes
- **Region Constraints**: Search within specific screen areas
- **Confidence Thresholds**: Adjustable match sensitivity
- **Fallback Strategies**: Alternative matching approaches

#### Intelligent Fallbacks
- **Position-based**: Fall back to recorded coordinates
- **Region-based**: Search in expanded areas
- **Scale-based**: Try different image scales
- **Time-based**: Retry with delays

#### Human-like Automation
- **Natural Movement**: Curved mouse paths
- **Variable Timing**: Random delays within ranges
- **Realistic Clicking**: Human-like click patterns
- **Scroll Smoothing**: Natural scrolling behavior

#### Chain Execution Features
- **Sequence Chaining**: Link multiple automation sequences
- **Loop Configuration**: Repeat sequences with custom counts
- **Inter-loop Delays**: Pauses between sequence repetitions
- **Conditional Execution**: Branch based on screen conditions

### Usage Modes

#### Command Line Usage
```bash
# Play single sequence
python -m player.main sequence.json

# Play chain configuration
python -m player.main chain_config.json

# Standalone mode
python player/player_standalone.py sequence.json
```

#### Programmatic Usage
```python
from player import SequencePlayer, MultiSequencePlayer

# Single sequence
player = SequencePlayer("my_sequence.json")
player.play_sequence()

# Multi-sequence chain
with open("chain.json") as f:
    config = json.load(f)
multi_player = MultiSequencePlayer(config)
multi_player.play_chain()
```

### Configuration Options

#### Visual Matching Settings
- **Match Threshold**: Confidence level for image matching (0.8 default)
- **Search Regions**: Constrain search to specific screen areas
- **Scale Ranges**: Min/max scales for multi-scale matching
- **Timeout Settings**: Max time to search for images

#### Execution Parameters
- **Loop Counts**: Number of sequence repetitions
- **Delay Settings**: Pauses between actions and sequences
- **Fallback Behavior**: Actions when visual matching fails
- **Error Handling**: Continue vs. stop on errors

## 🤖 AI Integration

LoOper includes sophisticated AI integration for intelligent automation decisions.

### LLM Integration (Ollama)

#### Model Support
- **Local Models**: Run AI models locally via Ollama
- **Multiple Models**: Support for various LLM models
- **Vision Models**: AI models that can analyze screenshots
- **Custom APIs**: Configurable API endpoints

#### LLM Node Features
- **Screen Analysis**: AI can analyze current screen state
- **Dynamic Decisions**: Make automation choices based on content
- **Text Generation**: Generate content for forms and inputs
- **Conditional Logic**: AI-powered workflow branching

#### Configuration Options
- **Model Selection**: Choose from available Ollama models
- **API Settings**: Configure connection parameters
- **Prompt Templates**: Customize AI instructions
- **Output Variables**: Store AI responses for later use

### Vision Capabilities
- **Screenshot Analysis**: AI can examine screen captures
- **Content Recognition**: Identify UI elements and text
- **Decision Making**: Choose actions based on visual content
- **Context Understanding**: Maintain awareness across workflow steps

## 📊 File Formats and Configuration

### Sequence Files (.json)
Recorded sequences are stored in structured JSON format:

```json
{
  "metadata": {
    "total_duration": 30.45,
    "start_time": "2024-01-15T14:30:00.123456",
    "action_count": 8,
    "recorder_version": "2.0",
    "created_by": "LoOper Recorder"
  },
  "actions": [
    {
      "type": "click",
      "x": 500,
      "y": 300,
      "button": "left",
      "timestamp": 1642252200.123,
      "screenshot": "screenshots/click_001.png"
    },
    {
      "type": "type",
      "text": "example text",
      "timestamp": 1642252201.456
    },
    {
      "type": "keystroke",
      "key": "enter",
      "timestamp": 1642252202.789
    },
    {
      "type": "scroll",
      "x": 400,
      "y": 200,
      "dx": 0,
      "dy": -3,
      "timestamp": 1642252203.012
    },
    {
      "type": "drag",
      "start_x": 300,
      "start_y": 200,
      "end_x": 500,
      "end_y": 400,
      "button": "left",
      "timestamp": 1642252204.345
    }
  ]
}
```

### Chain Configuration Files (.json)
Multi-sequence chains use configuration files:

```json
{
  "chain": [
    {
      "sequence_file": "login_sequence.json",
      "loops": 1,
      "extra_delay": 2.0
    },
    {
      "sequence_file": "data_entry.json",
      "loops": 5,
      "extra_delay": 1.0,
      "fallback_sequences": ["fallback_data_entry.json"]
    }
  ],
  "global_settings": {
    "match_threshold": 0.8,
    "max_retries": 3,
    "human_like_delays": true
  }
}
```

### Node Graph Files (.json)
Visual workflows are saved as graph structures:

```json
{
  "nodes": {
    "sequence_001": {
      "type": "SequenceNode",
      "name": "Login Process",
      "sequence_file": "login.json",
      "position": [100, 100]
    },
    "conditional_001": {
      "type": "ConditionalNode",
      "condition_type": "image_presence",
      "image_path": "success_indicator.png",
      "position": [300, 100]
    },
    "llm_001": {
      "type": "LLMNode",
      "model": "llama2",
      "prompt": "Analyze the screen and determine next action",
      "position": [500, 100]
    }
  },
  "connections": [
    {
      "source": "sequence_001",
      "target": "conditional_001"
    }
  ]
}
```

## 🎯 Usage Guide

### Getting Started

1. **Launch the Application**
   ```bash
   python main.py
   ```

2. **Create Your First Automation**
   - Click "File" → "New Chain" to start
   - Add nodes by right-clicking in the graph area
   - Connect nodes to create workflow

3. **Record Actions**
   - Click "Record Sequence" in the menu
   - Perform actions you want to automate
   - Press ESC to save the recording

4. **Execute Automation**
   - Click "Run Chain" to execute your workflow
   - Monitor execution in the console
   - Press ESC to stop execution if needed

### Advanced Workflows

#### Creating Conditional Logic
1. Add a Conditional Node to your graph
2. Configure the condition (image presence, OCR, etc.)
3. Connect different paths for true/false outcomes
4. Test the logic with various screen states

#### Using AI Integration
1. Add an LLM Node to your workflow
2. Configure the AI model and prompt
3. Enable vision if you want AI to analyze screenshots
4. Use AI output to make dynamic decisions

#### Setting Up Fallbacks
1. Open sequence properties dialog
2. Configure fallback actions for clicks
3. Set retry counts and timeouts
4. Test fallback behavior when images change

### Best Practices

#### Recording Quality Sequences
- **Stable Environment**: Record in consistent screen conditions
- **Clear Actions**: Make distinct, deliberate actions
- **Timing**: Allow natural pauses between actions
- **Screenshots**: Ensure unique visual elements for reliable matching

#### Workflow Design
- **Modular Sequences**: Break complex tasks into smaller sequences
- **Error Handling**: Always include fallback options
- **Testing**: Test workflows in different conditions
- **Documentation**: Use descriptive node names

### Node Inputs via Context Menu
- Inputs no longer appear directly on nodes; this improves visual clarity.
- Right-click a node to open its context menu and choose the relevant “Edit …” action.
- Sequence nodes: use “Edit Sequence Properties” to review and modify `loop_count` and `extra_delay`.
- Conditional, LLM, TTS, and Chain Import nodes: use their respective “Edit …” dialogs to modify configuration.
- All dialog changes synchronize with node properties and saved configurations automatically.
- Nodes with editable inputs show a subtle border accent to indicate configurability.

### Visual Simplification
- Port labels are hidden; colored dots indicate connection points. Names remain internally for logic and serialization.
- Node titles are centered. Title font is larger and black across nodes, except Sequence nodes which keep white text for contrast on dark grey.
- Input ports are blue; output ports are green. Conditional outputs: true is green, false is red.

#### Performance Optimization
- **Image Quality**: Use high-contrast screenshots for better matching
- **Region Constraints**: Limit search areas to improve speed
- **Reasonable Timeouts**: Balance reliability with speed
- **Cleanup**: Remove unused sequences and screenshots

## 🔧 Configuration and Customization

### Environment Variables
- `OLLAMA_API_URL`: Default URL for Ollama API (default: http://localhost:11434)

### Logging Configuration
- Comprehensive logging to `graphui_debug.log`
- Console output for real-time feedback
- Configurable log levels for debugging

### Visual Theme Customization
- Dark theme by default
- Customizable colors in `constants.py`
- Responsive UI scaling

### Performance Settings
- Template matching confidence thresholds
- Search timeout configurations
- Memory management for large workflows

## 🚨 Troubleshooting

### Common Issues

#### Recording Problems
- **No Screenshots**: Ensure PIL/Pillow is installed correctly
- **Missing Actions**: Check for rapid actions that might be grouped
- **ESC Not Working**: Ensure no other applications are capturing ESC

#### Playback Issues
- **Images Not Found**: Verify screenshot paths and image quality
- **Wrong Clicks**: Adjust match threshold or use region constraints
- **Timing Problems**: Increase delays between actions

#### GUI Issues
- **Nodes Not Appearing**: Check NodeGraphQt installation
- **Slow Performance**: Reduce graph complexity or restart application
- **Theme Problems**: Verify PyQt5 installation

### Debug Mode
Enable detailed logging by setting log level to DEBUG in configuration files.

### Getting Help
1. Check log files for error details
2. Verify all dependencies are installed
3. Test with simple sequences first
4. Review configuration settings

## 🔄 Updates and Maintenance

### Keeping Updated
- Regularly update dependencies via `pip install -r requirements.txt --upgrade`
- Check for new features and improvements
- Backup important automation sequences

### Data Management
- Regularly clean old screenshots
- Archive completed automation sequences
- Organize workflows in folders

## 🏆 Key Advantages

### User-Friendly
- **Visual Interface**: No coding required for basic automation
- **Intuitive Design**: Drag-and-drop workflow creation
- **Real-time Feedback**: Immediate visual feedback during operation

### Powerful Features
- **Computer Vision**: Reliable visual element detection
- **AI Integration**: Intelligent decision making
- **Modular Architecture**: Extensible and maintainable code

### Flexibility
- **Multiple Interfaces**: GUI and command-line options
- **Customizable**: Extensive configuration options
- **Cross-platform**: Works on various operating systems

### Reliability
- **Fallback Mechanisms**: Multiple strategies for robust operation
- **Error Recovery**: Graceful handling of failures
- **Comprehensive Logging**: Detailed operation tracking

---

**LoOper** transforms desktop automation from simple macro recording to intelligent, visual, AI-powered workflow orchestration. Whether you're automating repetitive tasks, testing applications, or creating complex workflows, LoOper provides the tools and flexibility you need for reliable automation solutions.