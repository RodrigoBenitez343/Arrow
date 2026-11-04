"""reasoning.py — the builder's model call.

One function, one contract: stream a generation from the local AI gateway and
return its text.  Every failure raises (``errors.py``): no gateway, no model,
non-200 status, malformed stream line, empty generation.  There is no
blocking fallback and no "return '' and carry on" path.
"""

import json

from .errors import BuilderConfigError, BuilderModelError, BuilderProtocolError

#: No timeout: a builder turn on a local engine may legitimately load and
#: generate for minutes.  Stop (the cancel hook) is the only limit.
TIMEOUT = None

_STREAM_ENDPOINT = '/generate_stream'
_MODELS_ENDPOINT = '/models'


def gateway_url():
    """The local AI gateway base URL (raises when the app has none)."""
    from AI.config_loader import get_api_url
    url = str(get_api_url() or '').rstrip('/')
    if not url:
        raise BuilderConfigError(
            'the local AI gateway URL is not configured — set it in the AI '
            'settings before using the builder')
    return url


def list_models():
    """Model names the gateway serves (raises on any failure)."""
    import requests
    url = gateway_url() + _MODELS_ENDPOINT
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise BuilderModelError('the gateway did not answer %s: %s'
                                % (url, exc)) from exc
    if response.status_code != 200:
        raise BuilderModelError(
            'the gateway answered %d on %s: %s'
            % (response.status_code, url, _body(response)))
    try:
        data = response.json()
    except ValueError as exc:
        raise BuilderModelError('the gateway model list is not JSON: %s'
                                % exc) from exc
    items = data.get('models') if isinstance(data, dict) else data
    names = []
    for item in (items or []):
        name = item.get('name') if isinstance(item, dict) else str(item)
        if name:
            names.append(str(name))
    if not names:
        raise BuilderModelError('the gateway serves no model (empty list)')
    return names


def default_model(engine):
    """The app's configured default LLM for ``engine`` (raises when unset)."""
    from player.multi_sequence.llm_executor_resources.config_utils import (
        get_default_llm_model)
    model = str(get_default_llm_model(engine or 'llamacpp') or '').strip()
    if not model:
        raise BuilderConfigError(
            'no default model is configured for engine %r — pick one in the '
            'builder settings' % (engine or 'llamacpp'))
    return model


def _body(response):
    return ' '.join(str(getattr(response, 'text', '') or '').split())[:300]


def raise_for_status(response, url):
    """Raise the provider's own words for a rejected call.

    A context-length rejection is the one failure that is not the chain's
    fault: the MODEL cannot hold the prompt (Groq's ``allam-2-7b`` has a 4k
    window, smaller than the analyst prompt on its own), so the message names
    the fix instead of repeating a JSON body.
    """
    body = _body(response)
    lowered = body.lower()
    if 'context_length' in lowered or 'reduce the length' in lowered:
        raise BuilderModelError(
            "the prompt is bigger than this model's context window — the "
            "Builder answers a described chain from its stored description "
            "instead (the provider said: %s)" % body)
    raise BuilderModelError('the gateway answered %d on %s: %s'
                            % (response.status_code, url, body))


def _provider(settings, model):
    """The explicit provider id for a model, or '' (the gateway default).

    A ``provider:model`` prefix IS the routing, so it wins over the stored
    engine: sending ``deepseek`` as the provider with a ``groq:`` model asked
    DeepSeek for a Groq model and the call came back empty.
    """
    try:
        from AI import byok
        prefix, _bare = byok.split_model_ref(model)
    except Exception:
        prefix, byok = '', None
    if prefix:
        return prefix
    if str(model).lower().endswith('.gguf'):
        # A local model file runs on the local engine, whatever is stored.
        return 'llamacpp'
    engine = str(settings.get('engine') or '').strip().lower()
    if engine and byok is not None:
        try:
            if byok.is_resolvable_provider_id(engine):
                return engine
        except Exception:
            pass
    return ''


def generate(prompt, system, settings, on_delta=None, cancel=None):
    """One streamed generation (raises on every failure)."""
    import requests

    settings = settings if isinstance(settings, dict) else {}
    model = str(settings.get('model') or '').strip() or default_model(
        settings.get('engine'))
    url = gateway_url() + _STREAM_ENDPOINT
    payload = {
        'model': model,
        'prompt': prompt,
        'system': system,
        'stream': True,
    }
    provider = _provider(settings, model)
    if provider:
        payload['provider'] = provider

    try:
        response = requests.post(url, json=payload, stream=True, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise BuilderModelError('the gateway call to %s failed: %s'
                                % (url, exc)) from exc
    if response.status_code != 200:
        raise_for_status(response, url)

    accumulated = ''
    cancelled = False
    try:
        for line in response.iter_lines(decode_unicode=True):
            if callable(cancel) and cancel():
                cancelled = True
                break                 # Stop: the caller keeps the partial
            if not line or not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise BuilderProtocolError(
                    'the gateway stream line is not JSON: %r (%s)'
                    % (line[:200], exc)) from exc
            if not isinstance(event, dict) or 'text' not in event:
                raise BuilderProtocolError(
                    'the gateway stream line has no "text": %r' % line[:200])
            if event.get('error'):
                # The provider refused mid-stream (a decommissioned model, a
                # quota, a bad key): say ITS words, not "no text".
                raise BuilderModelError(str(event['error']))
            text = str(event.get('text') or '')
            if text.startswith(accumulated):
                increment = text[len(accumulated):]
                accumulated = text
            else:
                increment = text
                accumulated += text
            if increment and callable(on_delta):
                on_delta(increment)
    finally:
        response.close()

    if not accumulated.strip():
        if cancelled:
            return ''             # the user stopped before any text arrived
        raise BuilderModelError(
            'the model returned no text (engine %r, model %r)'
            % (settings.get('engine'), model))
    return accumulated.strip()
