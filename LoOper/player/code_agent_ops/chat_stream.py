"""Streaming chat worker for the Code Node Studio agent.

Ollama goes through OllamaClient.chat_stream; llama.cpp goes through the
app's /llamacpp/generate_stream NDJSON route.  Both emit deltas; servers
that return the growing full text are deduped (only the new tail is
emitted).  The worker applies the little-coder-style failure handling:

- hard size cap per reply (degenerate-repetition guard);
- a reply that dies mid-stream is salvaged when it ends on a COMPLETE code
  fence (the model wrote the code, then rambled without EOS); otherwise the
  partial tail is surfaced with the error so a retry is diagnosable;
- the sampler policy (_adapt_sampler) makes every re-ask sample differently.
"""
import json
import logging
import re

from PyQt5.QtCore import QThread, pyqtSignal

from NGUI.i18n import _
from player.code_agent_ops.constants import (
    MAX_CHAT_STREAM_CHARS,
    STEER_EMPTY_CYCLE,
    STEER_MAXTOK_MAX,
    STEER_MAXTOK_STEP,
    STEER_PENALTY_BASE,
    STEER_PENALTY_MAX,
    STEER_PENALTY_STEP,
    STEER_TEMP_BASE,
)

logger = logging.getLogger(__name__)

__all__ = [
    '_ChatStreamWorker',
    '_failure_kind',
    '_adapt_sampler',
]


class _SteerError(Exception):
    """The reply diverged (oversized ramble) - abort with a correction."""
    pass


class _Aborted(RuntimeError):
    """Stop was pressed mid-stream - end the request, never retry it.
    (Subclasses RuntimeError so a missed catch can never be salvaged as a
    dead stream.)"""
    pass


def _failure_kind(msg):
    """Classify a failed stream for the retry policy: 'steer' = degenerate
    repetition (escalate the anti-repeat penalty), 'trunc' = the reply was cut
    by the token budget (grow the budget), 'empty' = the provider sent no text
    at all (a retry rarely helps - a wrong model/key does not fix itself),
    'none' = anything else (temperature wobble so the model is pushed off its
    rut)."""
    m = str(msg or '').lower()
    if 'steer:' in m:
        return 'steer'
    if 'empty reply' in m:
        return 'empty'
    if 'max_tokens' in m or 'truncat' in m:
        return 'trunc'
    return 'none'


def _adapt_sampler(steer, kind):
    """Move the sampling knobs before the next ask of a failed turn (bounded).

    A re-asked turn must never replay the same token sequence: 'trunc' grows
    the output budget, 'steer' raises the anti-repeat penalty and lowers the
    temperature (the classic degenerate-repetition escape), and any other
    failure wobbles the temperature along the explore/focus cycle while the
    penalty drifts up - so a stuck model gets *noise*, not an identical retry
    that fails identically until the budget dies.  Mutates and returns the
    steer dict; every field is clamped and 'maxtok' is ctx-clamped later by
    the caller's budget."""
    steer['steers'] = steer.get('steers', 0) + 1
    steer['tries'] = steer.get('tries', 0) + 1
    if kind == 'trunc':
        steer['maxtok'] = min(STEER_MAXTOK_MAX,
                              steer.get('maxtok', 1.0) * STEER_MAXTOK_STEP)
        # A repeated truncation usually means the model writes the artifact and
        # then keeps generating chatter without an EOS until the budget cuts
        # it - growing the budget alone replays the same ramble, so from the
        # second truncation the temperature also wobbles to change its voice.
        if steer['tries'] >= 2:
            steer['temp'] = STEER_EMPTY_CYCLE[(steer['tries'] - 2)
                                              % len(STEER_EMPTY_CYCLE)]
    elif kind == 'steer':
        steer['penalty'] = min(STEER_PENALTY_MAX,
                               steer.get('penalty', STEER_PENALTY_BASE)
                               + STEER_PENALTY_STEP)
        steer['temp'] = max(0.1, steer.get('temp', STEER_TEMP_BASE) - 0.05)
    else:
        steer['penalty'] = min(STEER_PENALTY_MAX,
                               steer.get('penalty', STEER_PENALTY_BASE)
                               + STEER_PENALTY_STEP * 0.5)
        steer['temp'] = STEER_EMPTY_CYCLE[(steer['tries'] - 1)
                                          % len(STEER_EMPTY_CYCLE)]
    return steer


class _ChatStreamWorker(QThread):
    """Stream a coding-agent reply token by token."""

    delta_stream = pyqtSignal(str)
    finished_stream = pyqtSignal(str)
    error_stream = pyqtSignal(str)

    def __init__(self, prompt, model, use_llamacpp, system, max_tokens=4096,
                 temperature=STEER_TEMP_BASE,
                 repeat_penalty=STEER_PENALTY_BASE, stop_ref=None):
        super().__init__()
        self._prompt = prompt
        self._model = model
        self._use_llamacpp = bool(use_llamacpp)
        self._system = system
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._repeat_penalty = repeat_penalty
        # Shared with the panel's Stop button: once 'flag' is set the stream
        # dies on the next chunk instead of running to completion.
        self._stop_ref = stop_ref

    def _stopped(self):
        try:
            return bool(self._stop_ref and self._stop_ref.get('flag'))
        except Exception:
            return False

    def run(self):
        acc = ''
        api = ''
        try:
            from NGUI.nodes_resources.base_node import get_default_api_url
            api = (get_default_api_url() or '').strip().rstrip('/')
        except Exception:
            api = ''

        def push(chunk):
            nonlocal acc
            if self._stopped():
                raise _Aborted(_('Stopped by user.'))
            if not chunk:
                return
            # Full-text servers echo the running text; emit only the tail.
            if acc and len(chunk) > len(acc) and chunk.startswith(acc):
                inc = chunk[len(acc):]
                acc = chunk
            elif chunk == acc:
                inc = ''
            else:
                acc += chunk
                inc = chunk
            if not inc:
                return
            # The llama.cpp engine yields '[Error: ...]' when the server aborts
            # mid-stream (context overflow, slot errors) - surface it instead
            # of finishing with a silently truncated reply.
            if inc.startswith('[Error:'):
                raise RuntimeError(inc)
            if len(acc) > MAX_CHAT_STREAM_CHARS:
                raise _SteerError(
                    'STEER: reply exceeded %d chars - repeating itself. '
                    'Correcting: reply with the code only.' %
                    MAX_CHAT_STREAM_CHARS)
            self.delta_stream.emit(inc)

        try:
            if not self._use_llamacpp:
                from AI.consult import OllamaClient
                client = OllamaClient(base_url=api)
                result = client.chat_stream(
                    model=self._model,
                    prompt=self._prompt,
                    system=self._system or '',
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    on_delta=push,
                )
                if result and result.get('error') and not acc:
                    self.error_stream.emit(str(result.get('error')))
                    return
            else:
                import requests as _req
                url = api + '/llamacpp/generate_stream'
                payload = {
                    'model': self._model,
                    'prompt': self._prompt,
                    'system': self._system or '',
                    'temperature': self._temperature,
                    'max_tokens': self._max_tokens,
                    # The panel escalates this penalty after each degenerate
                    # reply so the retry samples more deterministically.
                    'repeat_penalty': self._repeat_penalty,
                    'repeat_last_n': 512,
                }
                with _req.post(url, json=payload, stream=True,
                               timeout=None) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines(decode_unicode=True):
                        if self._stopped():
                            # Breaking here closes the streamed connection on
                            # context exit, so the server slot is released too.
                            break
                        if not line or not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        push(obj.get('text') or '')
            if self._stopped():
                self.error_stream.emit(_('Stopped by user.'))
                return
            if not acc.strip():
                # Zero text is not a normal finish: the panel would retry the
                # same call six times and end with "the agent did not produce
                # anything".  Name the cause instead of the symptom.
                self.error_stream.emit(
                    _('The model returned an empty reply ({}). Check the '
                      'model/provider in the Terminal page.').format(
                          self._model))
                return
        except _SteerError as e:
            self.error_stream.emit(str(e))
            return
        except _Aborted:
            self.error_stream.emit(_('Stopped by user.'))
            return
        except RuntimeError as e:
            # The stream died (truncation / context overflow / server abort).
            # Keep the partial when it ends on a COMPLETE code fence (the
            # model finished writing code, then rambled without EOS); the
            # panel's parsers still gate whatever was recovered.
            if acc and _has_closed_fences(acc):
                self.finished_stream.emit(acc)
                return
            msg = str(e)
            partial = acc
            i = partial.lower().rfind('[error:')
            if i >= 0:
                partial = partial[:i]
            if partial.strip():
                msg += '\n[partial tail] ' + partial.strip()[-300:]
            self.error_stream.emit(msg)
            return
        except Exception as e:
            self.error_stream.emit(str(e))
            return
        self.finished_stream.emit(acc)


def _has_closed_fences(text):
    """True when the accumulated text ends outside an open code fence
    (an even, non-zero number of fence markers)."""
    n = str(text or '').count('```')
    return n >= 2 and n % 2 == 0
