"""llama-server command line: placement flags must be explicit.

Measured failure: a 35B MoE asked for with gpu_layers=0.  The flag was omitted,
and llama.cpp's default is ``--gpu-layers 1024`` (fit everything to the GPU),
so the loader pushed ~11 GB onto an 8 GB iGPU and died with
``vk::Device::allocateMemory: ErrorOutOfDeviceMemory`` -> 503 on every call.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PyQt5.QtWidgets import QApplication  # noqa: E402  (engine imports Qt-free, but keep parity)

from AI import llama_cpp_engine as engine_mod  # noqa: E402
from AI.llama_cpp_engine import LlamaCppEngine  # noqa: E402

# The fake exe path has no --help output; pretend the built fork supports the
# device flag so the CPU-only assertions see the real command shape.
engine_mod._FLAG_SUPPORT[(r'C:\fake\llama-server.exe', '--device')] = True


class Cap:
    def __init__(self, model_type='text', is_moe=False, special=False):
        self.model_type = model_type
        self.is_moe = is_moe
        self.needs_special_tokens = special


def _engine(tmp_path, gpu=0, threads=-1, mmproj=''):
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'GGUF')            # not read by the builder
    e = LlamaCppEngine({'model_path': str(model), 'server_port': 8199,
                        'gpu_layers': gpu, 'threads': threads,
                        'mmproj_path': mmproj, 'batch_size': 2048})
    return e


def _cmd(e):
    return e._server_cmd(r'C:\fake\llama-server.exe')


def test_cpu_only_is_explicit_and_never_touches_vulkan(tmp_path):
    e = _engine(tmp_path, gpu=0)
    e.capability = Cap(is_moe=True)
    cmd = _cmd(e)
    i = cmd.index('--gpu-layers')
    assert cmd[i + 1] == '0', cmd           # the default of 1024 must be overridden
    assert '--device' in cmd and cmd[cmd.index('--device') + 1] == 'none'
    assert '-fit' in cmd and cmd[cmd.index('-fit') + 1] == 'off'
    assert '--cpu-moe' not in cmd           # nothing on a GPU, nothing to offload


def test_moe_with_gpu_keeps_experts_on_cpu(tmp_path):
    e = _engine(tmp_path, gpu=12)
    e.capability = Cap(is_moe=True)
    cmd = _cmd(e)
    assert '--cpu-moe' in cmd, cmd
    i = cmd.index('--gpu-layers')
    assert cmd[i + 1] == '12'
    assert cmd[cmd.index('-fit') + 1] == 'off'
    assert '--device' not in cmd


def test_dense_model_with_gpu_keeps_auto_fit(tmp_path):
    e = _engine(tmp_path, gpu=12)
    e.capability = Cap(is_moe=False)
    cmd = _cmd(e)
    assert '--cpu-moe' not in cmd and '-fit' not in cmd


def test_grounding_model_never_loads_the_projector_into_the_server(tmp_path):
    mm = tmp_path / 'mmproj-LocateAnything.gguf'
    mm.write_bytes(b'GGUF')
    e = _engine(tmp_path, gpu=0, mmproj=str(mm))
    e.capability = Cap(model_type='grounding', special=True)
    cmd = _cmd(e)
    assert '--mmproj' not in cmd, cmd
    assert '--special' in cmd


def test_multimodal_model_does_load_the_projector(tmp_path):
    mm = tmp_path / 'mmproj-LFM2.5-VL.gguf'
    mm.write_bytes(b'GGUF')
    e = _engine(tmp_path, gpu=0, mmproj=str(mm))
    e.capability = Cap(model_type='multimodal')
    cmd = _cmd(e)
    assert cmd[cmd.index('--mmproj') + 1] == str(mm)


def test_threads_default_to_physical_cores(tmp_path):
    e = _engine(tmp_path, gpu=0, threads=-1)
    e.capability = Cap()
    cmd = _cmd(e)
    n = int(cmd[cmd.index('--threads') + 1])
    try:
        import psutil
        physical = psutil.cpu_count(logical=False) or -1
    except Exception:
        physical = -1
    assert n == physical or n == -1


def test_explicit_threads_are_honoured(tmp_path):
    e = _engine(tmp_path, gpu=0, threads=3)
    e.capability = Cap()
    assert _cmd(e)[_cmd(e).index('--threads') + 1] == '3'


def test_no_device_flag_on_an_old_binary(tmp_path):
    """An older fork without --device must not get an unknown argument."""
    engine_mod._FLAG_SUPPORT[(r'C:\fake\llama-server.exe', '--device')] = False
    try:
        e = _engine(tmp_path, gpu=0)
        e.capability = Cap()
        cmd = _cmd(e)
        assert '--device' not in cmd, cmd
        assert cmd[cmd.index('--gpu-layers') + 1] == '0'
        assert '-fit' in cmd
    finally:
        engine_mod._FLAG_SUPPORT[(r'C:\fake\llama-server.exe', '--device')] = True


def test_command_without_a_capability_object(tmp_path):
    """Measured 500 on every chat: the engine had no `capability` and
    _server_cmd called `is_moe_model` (imported only inside another function)
    -> NameError -> \"llama.cpp error: name 'is_moe_model' is not defined\"."""
    e = _engine(tmp_path, gpu=0)
    e.capability = None
    cmd = _cmd(e)                      # must not raise
    assert '--model' in cmd
    assert cmd[cmd.index('--gpu-layers') + 1] == '0'


def test_reasoning_budget_is_capped_by_default(tmp_path):
    """Unbounded thinking ate the whole budget on every round (0 chars of
    answer, 25 minutes).  The server is started with a thinking cap."""
    engine_mod._FLAG_SUPPORT[(r'C:\fake\llama-server.exe',
                              '--reasoning-budget')] = True
    try:
        e = _engine(tmp_path, gpu=0)
        e.capability = Cap()
        cmd = _cmd(e)
        assert '--reasoning-budget' in cmd, cmd
        assert cmd[cmd.index('--reasoning-budget') + 1] == '2048'
    finally:
        engine_mod._FLAG_SUPPORT[(r'C:\fake\llama-server.exe',
                                  '--reasoning-budget')] = False


def test_reasoning_only_round_is_harvested_and_finalised(tmp_path, monkeypatch):
    """A round that reasoned to the token limit with no answer must not be
    continued (each continuation doubled the wait).  The harvest is written to
    a temp .md and ONE answer-only call produces the reply."""
    dumps = []
    monkeypatch.setattr(engine_mod, '_dump_reasoning',
                        lambda text: dumps.append(text) or 'C:\\tmp\\r.md')

    e = _engine(tmp_path, gpu=0)
    calls = []

    def fake_post(messages, max_tokens, temperature, top_p,
                  repeat_penalty=1.1, continue_final=False,
                  chat_template_kwargs=None):
        calls.append({'continue': continue_final,
                      'kwargs': chat_template_kwargs or {},
                      'msgs': messages})
        if len(calls) == 1:
            return True, {'usage': {}}, '', 'thinking hard', 'length'
        return True, {'usage': {}}, '```python\nadd()\n```', '', 'stop'

    e._post_chat_completion = fake_post
    ok, content, _reasoning, _usage, truncated = e._chat_with_continuation(
        [{'role': 'user', 'content': 'hi'}], 300, 0.2, 0.9)

    assert ok is True
    assert len(calls) == 2, calls           # round 1 + one finalisation
    assert calls[1]['kwargs'].get('enable_thinking') is False
    assert 'thinking hard' in calls[1]['msgs'][1]['content']   # harvest seeded
    assert 'add()' in content and truncated is False
    assert dumps and 'thinking hard' in dumps[0]


def test_harvest_file_is_written(tmp_path):
    path = engine_mod._dump_reasoning('step 1\nstep 2')
    assert path and os.path.isfile(path)
    body = open(path, encoding='utf-8').read()
    assert 'step 1' in body and 'Reasoning harvest' in body
    os.remove(path)


def test_qt_available():
    # The engine module itself must import without a QApplication.
    assert QApplication is not None
