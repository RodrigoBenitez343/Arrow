"""wrap.py — teach → wrap → import, and the from-scratch chain designer.

RECORDED teach: a recorded node (web_sequence / sequence) sitting on a
system-chain canvas is wrapped into its OWN sub-chain file and replaced by a
``chain_import`` wired to the orchestrator's ``tools`` port.
``wrap_recording`` does the same for a recording FILE with no canvas node —
the act loop's provider-driven demonstrations (``web_act``) land frozen
without a placeholder node ever existing.

DESIGNED teach (``build_step_chain``): an ``llm`` tool is a prompted
processing step (input → LLM → output, the instruction authored by the
builder chat, running on the app's local model by default) and a ``brain``
tool is a nested system chain with the subtask as its ``orch_goal`` —
imported wired to ``brains``.

The node -> chain-JSON serialization mirrors ``ConfigManager``'s savers
field by field (their output is what the player expects); the recording
FILE is never touched — it is the valuable artifact, the node was just a
handle for it.
"""

import json
import logging
import os
import random
import time

logger = logging.getLogger(__name__)

#: The recorded node kinds the wrapper accepts.
WRAPPABLE_TYPES = ('web_sequence', 'sequence')

#: Top-level arrays of a chain file (kept complete so loaders see the
#: same shape every saved chain has).
_SUBCHAIN_ARRAYS = (
    'sequences', 'conditional_nodes', 'llm_nodes', 'chain_import_nodes',
    'form_filler_nodes', 'code_nodes', 'container_nodes', 'context_nodes',
    'input_nodes', 'handle_nodes', 'mcp_nodes', 'output_nodes',
    'web_sequences',
)


def _gui_run(ctx, fn):
    """Run a canvas-touching read on the GUI thread when the ctx hooks it."""
    run = ctx.get('run_on_gui')
    if callable(run):
        try:
            return run(fn)
        except Exception:
            logger.debug('run_on_gui failed; inline fallback', exc_info=True)
    return fn()


def _prop(node, name, default=None):
    try:
        value = node.get_property(name)
    except Exception:
        value = None
    return default if value is None else value


def _prop_int(node, name, default=1):
    try:
        return int(float(_prop(node, name, default)))
    except Exception:
        return default


def _prop_float(node, name, default=1.0):
    try:
        return float(_prop(node, name, default))
    except Exception:
        return default


def _prop_bool(node, name, default=True):
    value = _prop(node, name, default)
    return str(value).strip().lower() in ('true', '1', 'yes', 'y', 'on')


def _basename(value):
    """Chain files store the recording's FILE NAME (ConfigManager parity)."""
    text = str(value or '').strip()
    if os.path.sep in text or '/' in text:
        return os.path.basename(text)
    return text


def _parse_items(value):
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _new_id():
    return '0x%012x' % (time.time_ns() ^ random.getrandbits(40))


def is_orphan(node):
    """True when nothing is wired OUT of the node (safe to replace)."""
    try:
        for port in node.output_ports():
            if port.connected_ports():
                return False
        return True
    except Exception:
        return False


def node_entry(node, node_type):
    """The chain-JSON entry for ONE recorded node (ConfigManager parity)."""
    try:
        position = list(node.pos())[:2]
    except Exception:
        position = [0.0, 0.0]
    common = {
        'node_id': _new_id(),
        'position': [float(position[0]), float(position[1])],
        'connections': [],
        'description': str(_prop(node, 'description', '') or ''),
    }
    if node_type == 'web_sequence':
        # Mirrors ConfigManager._save_web_sequence_node field by field.
        entry = {
            'name': node.name() or _prop(node, 'session_file', '')
                    or 'Web Sequence',
            'session_file': _basename(_prop(node, 'session_file', '')),
            'headless': _prop_bool(node, 'headless', False),
            'speed': _prop_float(node, 'speed', 1.0),
            'native_actions': _prop_bool(node, 'native_actions', True),
            'loop_count': _prop_int(node, 'loop_count', 1),
            'extra_delay': _prop_float(node, 'extra_delay', 1.0),
            'repeat_mode': str(_prop(node, 'repeat_mode', 'count') or 'count'),
            'extract_items': _parse_items(_prop(node, 'extract_items', '')),
        }
    else:
        # Mirrors ConfigManager._save_sequence_node field by field.
        entry = {
            'name': _prop(node, 'sequence_name', '') or node.name(),
            'sequence_file': _basename(_prop(node, 'sequence_file', '')),
            'loop_count': _prop_int(node, 'loop_count', 1),
            'extra_delay': _prop_float(node, 'extra_delay', 1.0),
            'use_app_opened': _prop_bool(node, 'use_app_opened', True),
            'click_drift_min': _prop_float(node, 'click_drift_min', 5.0),
            'click_drift_max': _prop_float(node, 'click_drift_max', 10.0),
            'actions': _prop(node, 'actions', []) or [],
        }
    entry.update(common)
    return entry


def subchain_config(node_type, entry, description):
    """A single-step chain file: the recorded node wired to an output."""
    out_id = _new_id()
    x, y = (entry.get('position') or [0.0, 0.0])[:2]
    output = {
        'type': 'output', 'label': '', 'variable_name': '',
        'agent_visible': True, 'overlay_visible': True,
        'popup_on_finish': True, 'show_rating': False,
        'render_mode': 'markdown',
        'node_id': out_id,
        'position': [float(x) + 420.0, float(y)],
        'connections': [],
    }
    entry['connections'] = [{'output_port': 'output',
                             'target_node_id': out_id,
                             'input_port': 'input'}]
    cfg = {key: [] for key in _SUBCHAIN_ARRAYS}
    key = 'web_sequences' if node_type == 'web_sequence' else 'sequences'
    cfg[key] = [entry]
    cfg['output_nodes'] = [output]
    cfg['collection'] = ''
    cfg['description'] = str(description or '')
    if node_type == 'web_sequence':
        cfg['_web_chain_scope'] = 'default'
    return cfg


def _unique_path(chains_dir, base_slug):
    path = os.path.join(chains_dir, base_slug + '.json')
    n = 2
    while os.path.exists(path):
        path = os.path.join(chains_dir, '%s_%d.json' % (base_slug, n))
        n += 1
    return path


def wrap_node(ctx, node_id, brain_id, trace=None, name_hint=''):
    """Wrap ONE recorded node into a wired ``chain_import``.

    Returns ``(lines, path)`` — ``path`` is the sub-chain file on success,
    ``''`` when there was nothing to wrap.  Never raises; every refusal is
    a readable trace line.
    """
    from player.builder.devtools import execute_builder_call
    from player.builder.node_templates import short_type, node_type_of, slugify
    from player.builder.trace import emit as _emit

    lines = []

    def _step(message):
        _emit(trace, message)
        lines.append(message)

    mgr = ctx.get('graph_manager')
    mgr = mgr() if callable(mgr) else mgr
    if mgr is None:
        _step('[wrap] no graph manager — cannot wrap %s' % node_id)
        return lines, ''
    node = _gui_run(ctx, lambda: mgr.get_node_by_id(str(node_id or '')))
    if node is None:
        _step('[wrap] no node %s on the canvas' % node_id)
        return lines, ''
    ntype = short_type(node_type_of(node))
    if ntype not in WRAPPABLE_TYPES:
        _step('[wrap] %s is a %s node — only recordings wrap' % (node_id, ntype))
        return lines, ''
    label = str(name_hint or _gui_run(ctx, lambda: node.name()) or ntype)
    file_key = 'session_file' if ntype == 'web_sequence' else 'sequence_file'
    other_key = 'sequence_file' if ntype == 'web_sequence' else 'session_file'
    rec_file = str(_gui_run(
        ctx, lambda: _prop(node, file_key, '')
        or _prop(node, other_key, '')) or '').strip()
    if not rec_file:
        _step('[wrap] "%s" has no recording yet — nothing to wrap'
              % label[:60])
        return lines, ''
    if not _gui_run(ctx, lambda: is_orphan(node)):
        _step('[wrap] "%s" is already wired — left alone' % label[:60])
        return lines, ''
    chains_dir = str(ctx.get('chains_dir') or '')
    if not chains_dir or not os.path.isdir(chains_dir):
        _step('[wrap] chains dir %r is not writable' % chains_dir)
        return lines, ''

    entry = _gui_run(ctx, lambda: node_entry(node, ntype))
    desc = entry.get('description') or label
    cfg = subchain_config(ntype, entry, desc)
    path = _unique_path(chains_dir, 'wrap_' + slugify(label))
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _step('[wrap] writing %s failed: %s' % (os.path.basename(path), e))
        return lines, ''
    _step('[wrap] "%s" → %s (recording %s)'
          % (label[:60], os.path.basename(path), _basename(rec_file)))

    stem = os.path.splitext(os.path.basename(path))[0]
    result = execute_builder_call(
        'create_node',
        {'node_type': 'chain_import', 'label': stem,
         'chain_file_path': path}, ctx)
    _step('[create_node] %s' % result)
    if str(result).startswith('('):
        return lines, ''
    import_id = str(ctx.get('state', {}).get('last_node_id') or '')
    if brain_id and import_id:
        _step('[wire] %s' % execute_builder_call(
            'wire', {'from_node_id': import_id, 'to_node_id': brain_id,
                     'input_port': 'tools'}, ctx))
    # Replace the bare recording with its import (the FILE stays).
    _step('[delete_node] %s' % execute_builder_call(
        'delete_node', {'node_id': node_id}, ctx))
    return lines, path


def wrap_recording(ctx, recording_file, label, kind='web_sequence',
                   brain_id='', trace=None):
    """Wrap a recording FILE into a wired tool import (no canvas node).

    The act loop's provider-driven demonstrations come as files, not nodes:
    this writes the same single-step sub-chain ``wrap_node`` writes (field
    parity with ``ConfigManager``'s savers), creates the ``chain_import``
    and wires it to the orchestrator's ``tools`` port.  The recording file
    itself is never touched.  Returns ``(lines, path)`` — '' when the file
    or the chains dir is unusable.  Never raises.
    """
    from player.builder.devtools import execute_builder_call
    from player.builder.node_templates import slugify
    from player.builder.trace import emit as _emit

    lines = []

    def _step(message):
        _emit(trace, message)
        lines.append(message)

    rec = str(recording_file or '').strip()
    if not rec or not os.path.isfile(rec):
        _step('[wrap] recording %r does not exist' % rec)
        return lines, ''
    chains_dir = str(ctx.get('chains_dir') or '')
    if not chains_dir or not os.path.isdir(chains_dir):
        _step('[wrap] chains dir %r is not writable' % chains_dir)
        return lines, ''

    label = str(label or '').strip() or 'demonstrated tool'
    entry = {
        'node_id': _new_id(),
        'position': [0.0, 0.0],
        'connections': [],
        'description': label,
    }
    if kind == 'web_sequence':
        entry.update({
            'name': label[:120],
            'session_file': _basename(rec),
            'headless': False, 'speed': 1.0, 'native_actions': True,
            'loop_count': 1, 'extra_delay': 1.0, 'repeat_mode': 'count',
            'extract_items': [],
        })
    else:
        entry.update({
            'name': label[:120],
            'sequence_file': _basename(rec),
            'loop_count': 1, 'extra_delay': 1.0, 'use_app_opened': True,
            'click_drift_min': 5.0, 'click_drift_max': 10.0, 'actions': [],
        })
    cfg = subchain_config(kind, entry, label)
    path = _unique_path(chains_dir, 'wrap_' + slugify(label))
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _step('[wrap] writing %s failed: %s' % (os.path.basename(path), e))
        return lines, ''
    _step('[wrap] "%s" → %s (recording %s)'
          % (label[:60], os.path.basename(path), _basename(rec)))

    stem = os.path.splitext(os.path.basename(path))[0]
    result = execute_builder_call(
        'create_node',
        {'node_type': 'chain_import', 'label': stem,
         'chain_file_path': path}, ctx)
    _step('[create_node] %s' % result)
    if str(result).startswith('('):
        return lines, ''
    import_id = str(ctx.get('state', {}).get('last_node_id') or '')
    if brain_id and import_id:
        _step('[wire] %s' % execute_builder_call(
            'wire', {'from_node_id': import_id, 'to_node_id': brain_id,
                     'input_port': 'tools'}, ctx))
    return lines, path


# ------------------------------------------------------ designed sub-chains

#: Full LLM-node field set (``ConfigManager._save_llm_node`` parity) so a
#: written file loads exactly like a canvas-saved one.  Deviations from the
#: saver defaults: ``screenshot_enabled`` False (a text processing step
#: neither screenshots nor types) — set per kind below.
_LLM_DEFAULTS = {
    'prompt': '', 'model': '', 'system_message': '',
    'temperature': 0.7, 'max_tokens': 4096, 'output_variable': 'llm_output',
    'api_url': '', 'write_text': True, 'use_vision': False,
    'vision_model': 'minicpm-v:latest', 'screenshot_enabled': False,
    'typing_batch_size': 20, 'typing_batch_delay': 0.05,
    'input_source': 'none', 'orchestrator_mode': False,
    'orch_max_steps': 15, 'orch_goal': '', 'orch_synthesize': True,
    'orch_synthesis_system': '', 'orch_use_goal_ledger': False,
    'orch_headless': False, 'web_mode': False, 'ocr_region': '',
    'web_ocr_locator': '', 'ocr_confidence': 0.5, 'ocr_preprocessing': True,
    'use_async': True, 'use_direct_rag': False, 'rag_embedding_model': '',
    'rag_chunk_size': 500, 'rag_overlap': 100, 'rag_top_k': 3,
    'rag_include_raw_input': False, 'rag_max_chars': 1500,
    'semantic_description': '', 'use_context_consolidation': False,
    'consolidation_chunk_size': 1000, 'consolidation_overlap': 200,
    'consolidation_top_k': 5, 'consolidation_max_tokens': 64,
    # Local-first: a designed step runs on the built-in engine with the
    # app's GGUF by default (a half-configured 'Ollama + empty model' node
    # was a visible mistake; the engine dialog can switch it any time).
    'use_llamacpp': True, 'llamacpp_model_path': '',
    'llamacpp_gpu_layers': 0, 'llamacpp_threads': -1,
    'llamacpp_context_size': 0,
}

#: Full Input-node field set (``ConfigManager._save_input_node`` parity):
#: ``passthrough`` True so the incoming value is forwarded, never typed.
_INPUT_DEFAULTS = {
    'type': 'input', 'label': '', 'default_value': '', 'user_prompt': '',
    'passthrough': True, 'web_mode': False, 'agent_modifiable': False,
    'decision_mode': False, 'decision_criterion': '',
    'decision_evaluator': 'llm', 'decision_model': '',
    'decision_default': False, 'question_mode': 'text', 'choices': '[]',
    'route_on_answer': False, 'accept_text': True, 'accept_images': False,
    'accept_documents': False, 'tts_enabled': False, 'tts_text': '',
    'tts_language': 'en', 'tts_voice_model': '', 'tts_speed': 1.0,
    'tts_speaker_id': None,
}

#: Full Output-node field set (``ConfigManager._save_output_node`` parity).
_OUTPUT_DEFAULTS = {
    'type': 'output', 'label': '', 'variable_name': '', 'agent_visible': True,
    'overlay_visible': True, 'popup_on_finish': True, 'show_rating': False,
    'render_mode': 'markdown', 'tts_enabled': False, 'tts_text': '',
    'tts_language': 'en', 'tts_voice_model': '', 'tts_speed': 1.0,
    'tts_speaker_id': None, 'tts_wait': False, 'image_source': '',
}


def _default_gguf():
    """The app's configured GGUF path (never raises; '' when unset)."""
    try:
        from AI.config_loader import get_llamacpp_config
        return str(get_llamacpp_config().get('model_path') or '').strip()
    except Exception:
        return ''


#: Full Code-node field set (``ConfigManager._save_code_node`` parity).
#: The authored code runs deterministically — no model in the loop at
#: run time; the engine fields only matter for the Studio's generator.
_CODE_DEFAULTS = {
    'type': 'code', 'code': '', 'file_path': '',
    'execute_on_input': True, 'output_variable': 'result', 'timeout': 30,
    'gen_model': '', 'gen_prompt': '', 'description': '',
    'use_llamacpp': False, 'llamacpp_model_path': '',
    'llamacpp_gpu_layers': 0, 'llamacpp_threads': -1,
    'dependencies': '[]', 'memory_context': '[]',
    'input_vars': [], 'output_vars': [],
}


def _port_names(value, default):
    """A sanitised port-name list; [default] when empty (never raises)."""
    if isinstance(value, str):
        value = [value]
    names = []
    for n in (value or []):
        n = str(n or '').strip()
        if n.isidentifier() and n not in names:
            names.append(n)
    return names or [default]


def code_chain_config(task, code, inputs=None, outputs=None):
    """A code-step chain: input -> CODE node (authored Python) -> output.

    The code step uses NAMED IO ports (the code node's IO editor): it reads
    the declared input variable(s) and assigns the declared output
    variable(s), defaults ``data`` in / ``result`` out.  Incoming value
    arrives over ``input.output -> code.<in>``; the result leaves over
    ``code.<out> -> output.input`` — never the base input/output exec edges.
    """
    in_names = _port_names(inputs, 'data')
    out_names = _port_names(outputs, 'result')
    code_in, code_out = in_names[0], out_names[0]
    in_id, code_id, out_id = _new_id(), _new_id(), _new_id()
    entry = dict(_CODE_DEFAULTS)
    entry.update({
        'code': str(code or ''),
        'description': str(task or ''),
        'node_id': code_id,
        'position': [360.0, 0.0],
        'input_vars': [{'name': n, 'type': 'any'} for n in in_names],
        'output_vars': [{'name': n, 'type': 'any'} for n in out_names],
        'connections': [{'output_port': code_out,
                         'target_node_id': out_id,
                         'input_port': 'input'}],
    })
    inp = dict(_INPUT_DEFAULTS)
    inp.update({
        'node_id': in_id,
        'position': [0.0, 0.0],
        'connections': [{'output_port': 'output',
                         'target_node_id': code_id,
                         'input_port': code_in}],
    })
    out = dict(_OUTPUT_DEFAULTS)
    out.update({'node_id': out_id, 'position': [720.0, 0.0],
                'connections': []})
    cfg = {key: [] for key in _SUBCHAIN_ARRAYS}
    cfg['input_nodes'] = [inp]
    cfg['code_nodes'] = [entry]
    cfg['output_nodes'] = [out]
    cfg['collection'] = ''
    cfg['description'] = str(task or '')
    return cfg


#: Conditional node fields for an LLM check (``_evaluate_llm_condition``
#: reads the FLAT llm_* keys; condition_type 'llm' selects that evaluator).
_COND_DEFAULTS = {
    'type': 'conditional', 'condition_type': 'llm', 'llm_prompt': '',
    'llm_engine': 'llamacpp', 'llm_model': '', 'llm_timeout': '10.0',
    'llm_use_vision': 'false',
}


def check_chain_config(task, model_path=''):
    """A check-step chain: input -> conditional (LLM criterion) -> outputs.

    The criterion IS the plan line's text; the taken branch passes the
    incoming value to its output node (``true`` / ``false`` ports), so the
    orchestrator can call the check and read the verdict.  The evaluator
    runs on the LOCAL engine by default — the build-time provider never
    leaks into the artifact.
    """
    in_id, cond_id, out_t, out_f = _new_id(), _new_id(), _new_id(), _new_id()
    cond = dict(_COND_DEFAULTS)
    cond.update({
        'llm_prompt': str(task or ''),
        'llm_model': str(model_path or ''),
        'node_id': cond_id,
        'position': [360.0, 0.0],
        'connections': [
            {'output_port': 'true', 'target_node_id': out_t,
             'input_port': 'input'},
            {'output_port': 'false', 'target_node_id': out_f,
             'input_port': 'input'},
        ],
    })
    inp = dict(_INPUT_DEFAULTS)
    inp.update({
        'node_id': in_id,
        'position': [0.0, 0.0],
        'connections': [{'output_port': 'output',
                         'target_node_id': cond_id,
                         'input_port': 'input'}],
    })
    out_true = dict(_OUTPUT_DEFAULTS)
    out_true.update({'node_id': out_t, 'label': 'true',
                     'position': [720.0, -120.0], 'connections': []})
    out_false = dict(_OUTPUT_DEFAULTS)
    out_false.update({'node_id': out_f, 'label': 'false',
                      'position': [720.0, 120.0], 'connections': []})
    cfg = {key: [] for key in _SUBCHAIN_ARRAYS}
    cfg['input_nodes'] = [inp]
    cfg['conditional_nodes'] = [cond]
    cfg['output_nodes'] = [out_true, out_false]
    cfg['collection'] = ''
    cfg['description'] = str(task or '')
    return cfg


def step_chain_config(task, kind='llm', prompt=''):
    """A designed sub-chain: input -> LLM step / sub-brain -> output.

    ``llm``: ONE prompted processing step.  Its ``prompt`` is the authored
    instruction; incoming data arrives on the llm's ``input`` port (the
    ``input.output -> llm.input`` wire — the ``data -> prompt`` feed is NOT
    wired so the instruction is never replaced).

    ``brain``: a nested system chain, the SAME skeleton the composer builds
    on canvases (input -> orchestrator LLM with ``orch_goal`` -> output),
    both feeds wired like the main skeleton.
    """
    in_id, llm_id, out_id = _new_id(), _new_id(), _new_id()
    llm = dict(_LLM_DEFAULTS)
    llm.update({
        'prompt': str(prompt or task or ''),
        'llamacpp_model_path': _default_gguf(),
        'node_id': llm_id,
        'position': [360.0, 0.0],
        'connections': [{'output_port': 'output',
                         'target_node_id': out_id,
                         'input_port': 'input'}],
    })
    if kind == 'brain':
        llm['orchestrator_mode'] = True
        llm['orch_goal'] = str(task or '')
    inp = dict(_INPUT_DEFAULTS)
    inp.update({
        'node_id': in_id,
        'position': [0.0, 0.0],
        'connections': [{'output_port': 'output',
                         'target_node_id': llm_id,
                         'input_port': 'input'}],
    })
    if kind == 'brain':
        inp['connections'].append({'output_port': 'data',
                                   'target_node_id': llm_id,
                                   'input_port': 'prompt'})
    out = dict(_OUTPUT_DEFAULTS)
    out.update({'node_id': out_id, 'position': [720.0, 0.0],
                'connections': []})
    cfg = {key: [] for key in _SUBCHAIN_ARRAYS}
    cfg['input_nodes'] = [inp]
    cfg['llm_nodes'] = [llm]
    cfg['output_nodes'] = [out]
    cfg['collection'] = ''
    cfg['description'] = str(task or '')
    return cfg


def _is_imported(ctx, path):
    """True when a chain_import on the canvas already points at ``path``."""
    want = os.path.normcase(os.path.abspath(str(path or '')))

    def _scan():
        mgr = ctx.get('graph_manager')
        mgr = mgr() if callable(mgr) else mgr
        if mgr is None:
            return False
        try:
            from player.builder.node_templates import short_type, node_type_of
            for node in (mgr.get_all_nodes() or []):
                try:
                    if short_type(node_type_of(node)) != 'chain_import':
                        continue
                    ref = str(_prop(node, 'chain_file_path', '')
                              or getattr(node, 'chain_file', '') or '')
                except Exception:
                    continue
                if ref and os.path.normcase(os.path.abspath(ref)) == want:
                    return True
        except Exception:
            pass
        return False

    return bool(_gui_run(ctx, _scan))


def build_step_chain(ctx, task, brain_id, kind='llm', prompt='', code='',
                     trace=None, inputs=None, outputs=None):
    """Write a DESIGNED sub-chain file and import it, wired to the brain.

    Kinds: ``llm`` (authored prompt), ``brain`` (nested orchestrator),
    ``code`` (authored Python, compile-checked by the caller), ``check``
    (one LLM conditional over the plan line's criterion).  Returns
    ``(lines, path)`` — '' when the chains dir is unwritable or the canvas
    refused the import.  Never raises; every outcome is a trace line.

    Rebuilds refresh the SAME file (same task → same slug) instead of
    spamming ``_2`` copies, and a step an earlier pass already imported is
    never imported twice.
    """
    from player.builder.devtools import execute_builder_call
    from player.builder.node_templates import slugify
    from player.builder.trace import emit as _emit

    lines = []

    def _step(message):
        _emit(trace, message)
        lines.append(message)

    chains_dir = str(ctx.get('chains_dir') or '')
    if not chains_dir or not os.path.isdir(chains_dir):
        _step('[build] chains dir %r is not writable' % chains_dir)
        return lines, ''
    kinds = {
        'brain': ('brain_', 'sub-brain'),
        'code': ('code_', 'code step'),
        'check': ('check_', 'check step'),
    }
    prefix, label = kinds.get(kind, ('llm_', 'LLM step'))
    base_path = os.path.join(chains_dir,
                             prefix + slugify(task) + '.json')
    refresh = False
    if os.path.exists(base_path):
        try:
            with open(base_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            refresh = str(existing.get('description') or '') == str(task or '')
        except Exception:
            refresh = False
    if kind == 'check':
        cfg = check_chain_config(task, model_path=_default_gguf())
    elif kind == 'code':
        cfg = code_chain_config(task, code, inputs=inputs, outputs=outputs)
    else:
        cfg = step_chain_config(task, kind=kind, prompt=prompt)
    path = base_path if refresh else _unique_path(
        chains_dir, prefix + slugify(task))
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _step('[build] writing %s failed: %s' % (os.path.basename(path), e))
        return lines, ''
    _step('[build] %s "%s" %s %s'
          % (label, str(task)[:60],
             'refreshed' if refresh else '→', os.path.basename(path)))

    # A step an earlier build pass already imported is already a tool on
    # this canvas — a second import would just duplicate it.
    if _is_imported(ctx, path):
        _step('[skip] "%s" already imported (%s)'
              % (str(task)[:60], os.path.basename(path)))
        return lines, path

    stem = os.path.splitext(os.path.basename(path))[0]
    result = execute_builder_call(
        'create_node',
        {'node_type': 'chain_import', 'label': stem,
         'chain_file_path': path}, ctx)
    _step('[create_node] %s' % result)
    if str(result).startswith('('):
        return lines, ''
    import_id = str(ctx.get('state', {}).get('last_node_id') or '')
    if brain_id and import_id:
        port = 'brains' if kind == 'brain' else 'tools'
        _step('[wire] %s' % execute_builder_call(
            'wire', {'from_node_id': import_id, 'to_node_id': brain_id,
                     'input_port': port}, ctx))
    return lines, path
