# LoOperV2 - Advanced Desktop Automation Platform

LoOperV2 is a comprehensive desktop automation and AI reasoning platform consisting of two main components:

- **LoOper** - Visual node-based automation platform with orthogonal wire routing
- **OverWatch** - Ollama API server with ComoRAG integration for advanced AI reasoning

## 🌟 Features

### LoOper
- 🎨 **Node-Based Visual Interface** - Intuitive drag-and-drop workflow creation
- 📹 **Action Recorder** - Record mouse, keyboard, and screen interactions
- ▶️ **Smart Playback** - Computer vision-based template matching
- 🤖 **AI Integration** - LLM-powered decision making
- 🔗 **Orthogonal Wire Routing** - Clean 90-degree connections between nodes

### OverWatch
- 🚀 **Ollama Integration** - High-performance local LLM inference
- 🧠 **ComoRAG System** - Cognitive-inspired memory-organized reasoning
- 🖥️ **PyQt5 GUI** - User-friendly model management interface
- 🌐 **FastAPI Server** - RESTful API with automatic documentation

## 📁 Project Structure

```
LoOperV2/
├── README.md              # This file
├── LoOper/                # Desktop automation platform
│   ├── NGUI/              # Node-based graphical interface
│   ├── recorder/          # Action recording module
│   ├── player/            # Playback and execution
│   ├── AI/                # AI integration components
│   └── README.MD          # LoOper documentation
└── OverWatch/             # Ollama + ComoRAG server
    ├── api/               # API endpoints and engines
    ├── config/            # Configuration management
    ├── ui/                # PyQt5 GUI application
    └── README.md          # OverWatch documentation
```

## 🚀 Quick Start

### Prerequisites
- Python 3.8 or higher
- Windows (primary support)
- Ollama server (for OverWatch)

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/vozimachinelearning/LoOperV2.git
   cd LoOperV2
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv venv
   .\venv\Scripts\Activate.ps1  # PowerShell
   # or
   venv\Scripts\activate.bat     # Command Prompt
   ```

3. **Install LoOper dependencies**:
   ```bash
   cd LoOper
   pip install -r requirements.txt
   ```

4. **Install OverWatch dependencies** (optional):
   ```bash
   cd ..\OverWatch
   pip install -r requirements.txt
   ```

### Running LoOper

#### Launch the Node-Based GUI
```bash
cd LoOper
python main.py
# or
python -m NGUI.app
```

The GUI features **orthogonal wire routing** that ensures all connections between nodes use clean 90-degree turns instead of diagonal lines, providing a professional and organized appearance.

#### Record Actions
```bash
python -m recorder.main
```

#### Playback Sequences
```bash
python -m player.main
```

### Running OverWatch

#### Start the API Server
```bash
cd OverWatch
python main.py
```

#### Launch the GUI
```bash
python launcher.py gui
```

## 🎨 LoOper - Node Graph Features

### Orthogonal Wire Routing
The node graph uses custom orthogonal wire routing that:
- Creates only 90-degree connections between nodes
- Provides clean horizontal and vertical wire segments
- Includes draggable control points for manual routing adjustments
- Snaps control points to a 10px grid for precision
- Displays live wire preview with orthogonal routing while connecting

This feature makes complex automation workflows easier to read and understand.

### Node Types
- **Sequence Nodes** - Group actions into reusable sequences
- **Action Nodes** - Individual automation actions (click, type, scroll)
- **Conditional Nodes** - Control flow based on screen conditions
- **LLM Nodes** - AI-powered decision making
- **Loop Nodes** - Repeated execution patterns
- **Trigger Nodes** - Event-based activation

## 📚 Documentation

For detailed documentation on each component:
- [LoOper Documentation](LoOper/README.MD)
- [OverWatch Documentation](OverWatch/README.md)
- [Player Module](LoOper/player/README.md)
- [Recorder Module](LoOper/recorder/README.md)

## 🔧 Development

### Key Technologies
- **PyQt5** - GUI framework for node graph and interfaces
- **NodeGraphQt** - Node-based graph visualization (patched for orthogonal routing)
- **OpenCV** - Computer vision and template matching
- **PyAutoGUI** - Screen automation and control
- **FastAPI** - REST API framework
- **Ollama** - Local LLM inference
- **FAISS** - Vector database for RAG

### Custom Modifications
The project includes custom modifications to NodeGraphQt for orthogonal wire routing:
- Custom pipe rendering with horizontal-first routing algorithm
- Control point system with grid snapping
- Override of default diagonal wire drawing

## 🤝 Contributing

This is an active development project. The orthogonal wire routing feature was recently implemented to improve workflow visualization.

## 📝 License

See individual component READMEs for licensing information.

## 🔗 Related Projects

- [NodeGraphQt](https://github.com/jchanvfx/NodeGraphQt) - Node graph framework
- [Ollama](https://ollama.ai) - Local LLM platform

---

**Author**: xXSup3rN0v4Xx  
**Repository**: https://github.com/vozimachinelearning/LoOperV2
