#!/usr/bin/env python3
"""
Settings and Configuration Management for OverWatch
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class OllamaSettings:
    """Ollama engine settings"""
    base_url: str = "http://localhost:11434"
    context_length: int = 4096
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1
    timeout: int = 300
    default_models: List[str] = field(default_factory=lambda: [
        "llama3.2:1b",
        "llama3.2:3b"
    ])


@dataclass
class APISettings:
    """API server settings"""
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    max_request_size: int = 10 * 1024 * 1024  # 10MB
    timeout: int = 300  # 5 minutes


@dataclass
class LoggingSettings:
    """Logging configuration"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class Settings:
    """Main settings container"""
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    api: APISettings = field(default_factory=APISettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    # Additional properties for backward compatibility
    @property
    def API_PORT(self) -> int:
        return self.api.port


def get_config_path() -> Path:
    """Get the configuration file path"""
    # Check environment variable first
    config_path = os.getenv('OVERWATCH_CONFIG_PATH')
    if config_path:
        return Path(config_path)

    # Default to config.json in the same directory as this file
    candidates = [Path(__file__).parent / 'config.json']
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        for prefix in ["", "_internal", "arrow", "_internal/arrow"]:
            candidates.append(Path(exe_dir) / prefix / "AI" / "config.json")
            candidates.append(Path(exe_dir) / prefix / "AI" / "OverWatch" / "config" / "config.json")
        # One-file builds: files are inside sys._MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ["", "LoOper"]:
                candidates.append(Path(meipass) / sub / "AI" / "config.json")
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def load_config_from_file(config_path: Path) -> Dict[str, Any]:
    """Load configuration from JSON file"""
    if not config_path.exists():
        logger.info(f"Config file not found at {config_path}, using defaults")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"Loaded configuration from {config_path}")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Failed to load config file {config_path}: {e}")
        return {}


def load_config_from_env() -> Dict[str, Any]:
    """Load configuration from environment variables"""
    config = {}
    
    # API settings
    if os.getenv('OVERWATCH_HOST'):
        config.setdefault('api', {})['host'] = os.getenv('OVERWATCH_HOST')
    if os.getenv('OVERWATCH_PORT'):
        config.setdefault('api', {})['port'] = int(os.getenv('OVERWATCH_PORT'))
    if os.getenv('OVERWATCH_DEBUG'):
        config.setdefault('api', {})['debug'] = os.getenv('OVERWATCH_DEBUG').lower() == 'true'
    
    # vLLM settings
    if os.getenv('VLLM_GPU_MEMORY_UTILIZATION'):
        config.setdefault('vllm', {})['gpu_memory_utilization'] = float(os.getenv('VLLM_GPU_MEMORY_UTILIZATION'))
    if os.getenv('VLLM_MAX_MODEL_LEN'):
        config.setdefault('vllm', {})['max_model_len'] = int(os.getenv('VLLM_MAX_MODEL_LEN'))
    if os.getenv('VLLM_TENSOR_PARALLEL_SIZE'):
        config.setdefault('vllm', {})['tensor_parallel_size'] = int(os.getenv('VLLM_TENSOR_PARALLEL_SIZE'))
    
    # Logging settings
    if os.getenv('LOG_LEVEL'):
        config.setdefault('logging', {})['level'] = os.getenv('LOG_LEVEL')
    if os.getenv('LOG_FILE'):
        config.setdefault('logging', {})['file_path'] = os.getenv('LOG_FILE')
    
    return config


def merge_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge multiple configuration dictionaries"""
    result = {}
    
    for config in configs:
        for key, value in config.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_configs(result[key], value)
            else:
                result[key] = value
    
    return result


def create_settings_from_dict(config_dict: Dict[str, Any]) -> Settings:
    """Create Settings object from configuration dictionary"""
    settings = Settings()
    
    # Update Ollama settings
    if 'ollama' in config_dict:
        ollama_config = config_dict['ollama']
        for key, value in ollama_config.items():
            if hasattr(settings.ollama, key):
                setattr(settings.ollama, key, value)
    
    # Backward compatibility: support 'vllm' key for migration
    if 'vllm' in config_dict:
        vllm_config = config_dict['vllm']
        # Map vLLM settings to Ollama settings where possible
        if 'max_model_len' in vllm_config:
            settings.ollama.context_length = vllm_config['max_model_len']
        if 'default_models' in vllm_config:
            settings.ollama.default_models = vllm_config['default_models']
    
    # Update API settings
    if 'api' in config_dict:
        api_config = config_dict['api']
        for key, value in api_config.items():
            if hasattr(settings.api, key):
                setattr(settings.api, key, value)
    
    # Update logging settings
    if 'logging' in config_dict:
        logging_config = config_dict['logging']
        for key, value in logging_config.items():
            if hasattr(settings.logging, key):
                setattr(settings.logging, key, value)
    
    return settings


def save_settings(settings: Settings, config_path: Optional[Path] = None) -> bool:
    """Save settings to configuration file"""
    if config_path is None:
        config_path = get_config_path()
    
    try:
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert settings to dictionary
        config_dict = asdict(settings)
        
        # Write to file
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Settings saved to {config_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save settings to {config_path}: {e}")
        return False


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Load settings from file and environment variables"""
    if config_path is None:
        config_path = get_config_path()
    
    # Load from file
    file_config = load_config_from_file(config_path)
    
    # Load from environment
    env_config = load_config_from_env()
    
    # Merge configurations (env overrides file)
    merged_config = merge_configs(file_config, env_config)
    
    # Create settings object
    settings = create_settings_from_dict(merged_config)
    
    logger.info("Settings loaded successfully")
    return settings


def setup_logging(settings: Settings):
    """Setup logging based on settings"""
    log_level = getattr(logging, settings.logging.level.upper(), logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(settings.logging.format)
    
    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if settings.logging.file_path:
        try:
            from logging.handlers import RotatingFileHandler
            
            # Ensure log directory exists
            log_path = Path(settings.logging.file_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                settings.logging.file_path,
                maxBytes=settings.logging.max_file_size,
                backupCount=settings.logging.backup_count
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            
            logger.info(f"Logging to file: {settings.logging.file_path}")
            
        except Exception as e:
            logger.error(f"Failed to setup file logging: {e}")


def validate_settings_simple(settings: Settings) -> bool:
    """Validate settings configuration"""
    try:
        # Validate API settings
        if not (1 <= settings.api.port <= 65535):
            logger.error(f"Invalid API port: {settings.api.port}")
            return False
        
        # Validate vLLM settings
        if not (0.0 < settings.vllm.gpu_memory_utilization <= 1.0):
            logger.error(f"Invalid GPU memory utilization: {settings.vllm.gpu_memory_utilization}")
            return False
        
        logger.info("Settings validation passed")
        return True
        
    except Exception as e:
        logger.error(f"Settings validation failed: {e}")
        return False


def get_api_url(settings: Settings) -> str:
    """Get API URL from settings (local gateway; 0.0.0.0 -> localhost)"""
    host = settings.api.host or "0.0.0.0"
    if host == "0.0.0.0":
        host = "localhost"
    return f"http://{host}:{settings.api.port}"


def validate_settings(settings: Settings) -> List[str]:
    """Validate settings and return list of issues"""
    issues = []
    
    # Validate Ollama settings
    if settings.ollama.context_length < 512:
        issues.append("Ollama context length should be at least 512")
    
    if not 0.0 <= settings.ollama.temperature <= 2.0:
        issues.append("Ollama temperature must be between 0.0 and 2.0")
    
    if not 0.0 <= settings.ollama.top_p <= 1.0:
        issues.append("Ollama top_p must be between 0.0 and 1.0")
    
    if settings.ollama.timeout < 30:
        issues.append("Ollama timeout should be at least 30 seconds")
    
    # Validate API settings
    if not 1024 <= settings.api.port <= 65535:
        issues.append("API port must be between 1024 and 65535")
    
    if settings.api.timeout < 10:
        issues.append("API timeout should be at least 10 seconds")

    return issues


# Create a default settings instance for easy access
_default_settings = None


def get_default_settings() -> Settings:
    """Get the default settings instance"""
    global _default_settings
    if _default_settings is None:
        _default_settings = load_settings()
    return _default_settings


def reload_settings() -> Settings:
    """Reload settings from configuration"""
    global _default_settings
    _default_settings = load_settings()
    return _default_settings