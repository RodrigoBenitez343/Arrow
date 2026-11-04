"""settings.py — the builder's engine choice.

Two keys only: which engine the builder's model call routes to, and which
model (blank = that engine's configured default).  The file lives in the
durable runtime dir and is written atomically.  A corrupt file raises — the
builder never silently falls back to defaults over the user's settings.
"""

import json
import os

from .errors import BuilderConfigError

#: (id, label) fallback list, used only when the app's engine manifest is
#: unavailable at *import* time; the panel prefers ``engine_options``.
ENGINES = (
    ('llamacpp', 'llama.cpp (GGUF)'),
    ('ollama', 'Ollama'),
)

DEFAULTS = {
    'engine': 'llamacpp',
    'model': '',       # '' = whatever the engine serves by default
}

_FILENAME = 'builder_settings.json'


def path():
    """The settings file path (raises when the runtime dir is unavailable)."""
    from AI.runtime_paths import get_runtime_dir
    base = get_runtime_dir()
    if not base:
        raise BuilderConfigError('the app runtime directory is unavailable')
    return os.path.join(base, _FILENAME)


def load():
    """The persisted settings (defaults only when the file does not exist)."""
    target = path()
    if not os.path.isfile(target):
        return dict(DEFAULTS)
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            saved = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BuilderConfigError(
            'could not read %s: %s' % (target, exc)) from exc
    if not isinstance(saved, dict):
        raise BuilderConfigError(
            '%s holds %r, not an object' % (target,
                                            type(saved).__name__))
    data = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in saved:
            data[key] = saved[key]
    return _consistent(data)


def _consistent(data):
    """A model that names a provider IS that provider.

    ``engine`` and ``model`` are two views of ONE choice: a ``groq:`` prefix on
    the model is what routes the call.  A file left saying ``deepseek`` +
    ``groq:allam-2-7b`` (a model picked from an unfiltered list) asked the
    gateway for a groq model at DeepSeek and failed every call.  The prefix is
    the more specific answer, so it wins.
    """
    model = str(data.get('model') or '')
    try:
        from AI import byok
        prefix, _bare = byok.split_model_ref(model)
    except Exception:
        prefix = ''
    if prefix:
        data['engine'] = prefix
    return data


def save(values):
    """Merge ``values`` over the stored settings and persist them."""
    data = load()
    if isinstance(values, dict):
        for key in DEFAULTS:
            if key in values:
                data[key] = values[key]
    data = _consistent(data)
    target = path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    except OSError as exc:
        raise BuilderConfigError(
            'could not write %s: %s' % (target, exc)) from exc
    return data
