# OverWatch - Ollama API with ComoRAG Integration

🔍 **OverWatch** is a comprehensive Ollama API server with FastAPI and ComoRAG (Cognitive-Inspired Memory-Organized RAG) integration for advanced language model inference and reasoning.

## Features

- **🚀 Ollama Integration**: High-performance inference for local language models
- **🧠 ComoRAG System**: Advanced reasoning with dynamic memory workspace
- **🖥️ PyQt5 GUI**: User-friendly interface for model management and reasoning
- **🌐 FastAPI Server**: RESTful API with automatic documentation
- **🔗 Tailscale Support**: Secure LAN networking for remote access
- **⚙️ Flexible Configuration**: JSON-based settings with environment variable support
- **📊 Real-time Monitoring**: Memory usage, model status, and system health

## Architecture

```
OverWatch/
├── main.py                 # FastAPI server entry point
├── launcher.py             # Application launcher script
├── requirements.txt        # Python dependencies
├── api/                    # API modules
│   ├── __init__.py
│   ├── ollama_engine.py   # Ollama model management
│   ├── comorag_engine.py  # ComoRAG reasoning engine
│   └── api_client.py      # API client for GUI
├── config/                 # Configuration management
│   ├── __init__.py
│   ├── settings.py        # Settings classes and utilities
│   └── config.json        # Default configuration
└── ui/                     # PyQt5 GUI application
    ├── __init__.py
    ├── app.py             # Main application class
    ├── main_window.py     # Main window implementation
    └── widgets.py         # Custom UI widgets
```

## Installation

### Prerequisites

- Python 3.8 or higher
- Ollama server installed locally
- 8GB+ RAM (16GB+ recommended)

### Quick Setup

1. **Clone or navigate to the OverWatch directory**:
   ```bash
   cd /path/to/LoOper/OverWatch
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Check system compatibility**:
   ```bash
   python launcher.py check
   ```

4. **View system status**:
   ```bash
   python launcher.py status
   ```

### Ollama Setup

1. **Install Ollama**:
   ```bash
   curl -fsSL https://ollama.ai/install.sh | sh
   ```

2. **Start Ollama service**:
   ```bash
   ollama serve
   ```

3. **Pull a model** (optional, OverWatch will auto-pull):
   ```bash
   ollama pull llama3.2:1b
   ```

### GPU Setup (Optional but Recommended)

For optimal performance with Ollama:

```bash
# For CUDA 11.8
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# For CUDA 12.1
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install GPU-accelerated FAISS
pip uninstall faiss-cpu
pip install faiss-gpu
```

## Usage

### Quick Start

**Start both API server and GUI**:
```bash
python launcher.py both
```

**Start API server only**:
```bash
python launcher.py api
```

**Start GUI only**:
```bash
python launcher.py gui
```

### API Server

The FastAPI server provides the following endpoints:

- **Health Check**: `GET /health`
- **List Models**: `GET /models`
- **Chat Completion**: `POST /chat`
- **Text Generation**: `POST /generate`
- **ComoRAG Reasoning**: `POST /comorag/reason`
- **Memory Management**: `GET/DELETE /comorag/memory`

#### Example API Usage

```python
import requests

# Health check
response = requests.get("http://localhost:8000/health")
print(response.json())

# Chat completion
chat_data = {
    "messages": [
        {"role": "user", "content": "What is machine learning?"}
    ],
    "temperature": 0.7,
    "max_tokens": 512
}
response = requests.post("http://localhost:8000/chat", json=chat_data)
print(response.json())

# ComoRAG reasoning
reasoning_data = {
    "query": "Explain the relationship between AI and machine learning",
    "max_cycles": 5
}
response = requests.post("http://localhost:8000/comorag/reason", json=reasoning_data)
print(response.json())
```

### GUI Application

The PyQt5 GUI provides:

1. **Model Management**: Load/unload HuggingFace models
2. **Reasoning Interface**: Interactive ComoRAG reasoning
3. **Memory Viewer**: Inspect ComoRAG memory state
4. **System Logs**: Real-time application logging
5. **Settings**: Configure API and model parameters

## Configuration

### Configuration File

Edit `config/config.json` to customize settings:

```json
{
  "api": {
    "host": "0.0.0.0",
    "port": 8000,
    "debug": false
  },
  "vllm": {
    "gpu_memory_utilization": 0.8,
    "max_model_len": 4096,
    "default_models": []
  },
  "comorag": {
    "memory_size": 1000,
    "max_reasoning_cycles": 5,
    "embedding_model": "all-MiniLM-L6-v2"
  },
  "tailscale": {
    "enabled": false,
    "server_ip": "",
    "api_port": 8000
  }
}
```

### Environment Variables

Override settings with environment variables:

```bash
export OVERWATCH_API_HOST="127.0.0.1"
export OVERWATCH_API_PORT="8080"
export OVERWATCH_VLLM_GPU_MEMORY="0.9"
export TAILSCALE_SERVER_IP="100.64.0.1"
```

## ComoRAG (Cognitive-Inspired Memory-Organized RAG)

ComoRAG implements advanced reasoning capabilities based on the research paper: [https://arxiv.org/abs/2508.10419](https://arxiv.org/abs/2508.10419)

### Key Features

- **Dynamic Memory Workspace**: Maintains context across reasoning cycles
- **Multi-Cycle Reasoning**: Iterative query refinement and evidence gathering
- **Confidence Scoring**: Evaluates reasoning quality
- **Memory Consolidation**: Efficient storage and retrieval of reasoning artifacts

### Usage Example

```python
from api.api_client import OverWatchAPIClient

client = OverWatchAPIClient("http://localhost:8000")

# Perform ComoRAG reasoning
result = client.comorag_reason(
    query="How does climate change affect ocean ecosystems?",
    context="Focus on marine biodiversity and food chains",
    max_cycles=7
)

print(f"Result: {result['result']}")
print(f"Confidence: {result['confidence']}")
print(f"Reasoning cycles: {len(result['reasoning_cycles'])}")
```

## Tailscale Integration

For secure remote access:

1. **Enable Tailscale in configuration**:
   ```json
   {
     "tailscale": {
       "enabled": true,
       "server_ip": "100.64.0.1",
       "api_port": 8000
     }
   }
   ```

2. **Set environment variables**:
   ```bash
   export TAILSCALE_SERVER_IP="your-tailscale-ip"
   export TAILSCALE_API_PORT="8000"
   ```

3. **Access remotely**:
   ```bash
   curl http://your-tailscale-ip:8000/health
   ```

## Advanced Usage

### Custom Model Loading

```bash
# Start with specific models
python launcher.py api --debug

# In another terminal, load models via API
curl -X POST "http://localhost:8000/models/load" \
     -H "Content-Type: application/json" \
     -d '{"model_name": "microsoft/DialoGPT-medium"}'
```

### Performance Tuning

1. **GPU Memory**: Adjust `vllm.gpu_memory_utilization` (0.1-0.9)
2. **Model Length**: Set `vllm.max_model_len` based on your use case
3. **ComoRAG Memory**: Tune `comorag.memory_size` for your workload
4. **Reasoning Cycles**: Balance `max_reasoning_cycles` vs. response time

### Monitoring

```bash
# Check system status
python launcher.py status

# Monitor API logs
tail -f logs/overwatch.log

# Memory usage via API
curl http://localhost:8000/memory
```

## Troubleshooting

### Common Issues

1. **CUDA Out of Memory**:
   - Reduce `gpu_memory_utilization`
   - Use smaller models
   - Decrease `max_model_len`

2. **PyQt5 Import Error**:
   ```bash
   pip install PyQt5 PyQt5-tools
   ```

3. **vLLM Installation Issues**:
   ```bash
   pip install vllm --no-build-isolation
   ```

4. **FAISS Not Found**:
   ```bash
   pip install faiss-cpu  # or faiss-gpu
   ```

### Debug Mode

```bash
# Enable debug logging
python launcher.py api --debug --log-level DEBUG

# Check configuration
python -c "from config.settings import load_settings; print(load_settings())"
```

### Performance Issues

1. **Slow Model Loading**: Use SSD storage, increase RAM
2. **High Memory Usage**: Reduce model size, tune memory settings
3. **Slow Reasoning**: Decrease reasoning cycles, optimize queries

## Development

### Project Structure

- `main.py`: FastAPI application with lifespan management
- `api/vllm_engine.py`: vLLM model loading and inference
- `api/comorag_engine.py`: ComoRAG reasoning implementation
- `api/api_client.py`: HTTP client for GUI communication
- `ui/`: PyQt5 GUI components
- `config/`: Configuration management

### Adding New Features

1. **New API Endpoints**: Add to `main.py`
2. **GUI Components**: Extend `ui/widgets.py`
3. **Configuration Options**: Update `config/settings.py`
4. **Model Support**: Modify `api/ollama_engine.py`

### Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests (when implemented)
pytest tests/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is part of the LoOper ecosystem. See the main LoOper repository for license information.

## References

- [Ollama Documentation](https://ollama.ai/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [ComoRAG Paper](https://arxiv.org/abs/2508.10419)
- [PyQt5 Documentation](https://doc.qt.io/qtforpython/)
- [Tailscale Documentation](https://tailscale.com/kb/)

## Support

For issues and questions:

1. Check the troubleshooting section
2. Review the configuration documentation
3. Enable debug mode for detailed logs
4. Check system compatibility with `python launcher.py status`

---

**OverWatch** - Empowering advanced AI reasoning with Ollama and ComoRAG 🔍🧠