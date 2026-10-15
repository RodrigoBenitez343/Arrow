"""settings.py — the builder's own model / engine settings.

Persisted in the durable runtime dir (the project rule: every writable output
goes through ``AI.runtime_paths.get_runtime_dir``), so the build chat can pick
the model it reasons with without touching the chain configuration.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

#: (id, label) for the engine picker.
ENGINES = (
    ('llamacpp', 'llama.cpp (GGUF)'),
    ('ollama', 'Ollama'),
)

DEFAULTS = {
    'engine': 'llamacpp',
    'model': '',          # '' = whatever the engine serves by default
    'reasoning': True,    # ask the reasoning LLM for the next step
    'max_steps': 6,
}

_FILENAME = 'builder_settings.json'


def path():
    """The settings file (durable, writable — runtime-dir rule)."""
    try:
        from AI.runtime_paths import get_runtime_dir
        base = get_runtime_dir()
    except Exception:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'data')
    return os.path.join(base, _FILENAME)


def load():
    """Persisted settings with defaults filled in (never raises)."""
    data = dict(DEFAULTS)
    try:
        with open(path(), 'r', encoding='utf-8') as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for key in DEFAULTS:
                if key in saved:
                    data[key] = saved[key]
    except Exception:
        pass
    return data


def save(values):
    """Merge ``values`` over the stored settings and persist (never raises)."""
    data = load()
    if isinstance(values, dict):
        for key in DEFAULTS:
            if key in values:
                data[key] = values[key]
    try:
        target = path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception:
        logger.warning('builder settings not saved', exc_info=True)
    return data
