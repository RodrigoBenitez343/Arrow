"""Look at the window a GUI code node just opened, so the agent can debug it.

Auto-run skips windowed code (a window popping open mid-edit is rude), which
used to leave a GUI task with NO feedback loop at all: the model wrote PyQt
code, nothing ran, and the user had to launch it and describe the screen.
This launches the script in the node venv, waits for its window, screenshots
that window, and asks the vision model (LFM2.5-VL) what is on screen - a text
report the coding agent can act on.  The app is killed afterwards.

Everything here is plain functions (no Qt); the panel runs it on a worker.
"""
import os
import subprocess
import sys
import tempfile
import time

logger = __import__('logging').getLogger(__name__)

try:  # LoOper/ on sys.path: the app and the test suite both run that way
    from platform_gate import screenshot as capture_screen
    from platform_gate import windows_for_pid
    from platform_gate import list_windows
except ImportError:  # imported as LoOper.*
    from LoOper.platform_gate import screenshot as capture_screen
    from LoOper.platform_gate import windows_for_pid
    from LoOper.platform_gate import list_windows

VISION_MODEL = 'LFM2.5-VL-450M-GGUF/LFM2.5-VL-450M-Q8_0.gguf'
_WAIT_S = 8.0            # window must appear within this
_SETTLE_S = 0.7          # let the first paint land before the screenshot

_SYSTEM = (
    'You review a screenshot of a desktop app that a script just opened. '
    'Answer in exactly this shape:\n'
    'VERDICT: OK  (or)  VERDICT: PROBLEM\n'
    'then at most 3 short lines: window title; the visible text, buttons and '
    'fields; anything broken. Say PROBLEM for an error dialog, a traceback, an '
    'empty/blank window, or a window still mid-load. Say OK when the app shows '
    'a usable window. No markdown, no preamble.'
)


def venv_python(site_packages):
    """Python executable of the venv that owns *site_packages*."""
    if site_packages:
        venv = os.path.dirname(os.path.dirname(os.path.abspath(site_packages)))
        exe = os.path.join(venv, 'Scripts', 'python.exe')
        if os.path.isfile(exe):
            return exe
        exe = os.path.join(venv, 'bin', 'python')
        if os.path.isfile(exe):
            return exe
    return sys.executable


def _pids_of(pid):
    """The launcher plus its descendants.

    A venv's ``Scripts/python.exe`` is a redirector: the real interpreter - and
    therefore the window - belongs to a CHILD pid, so matching only the
    launcher pid never finds anything.
    """
    pids = {pid}
    try:
        import psutil
        for child in psutil.Process(pid).children(recursive=True):
            pids.add(child.pid)
    except Exception:
        pass
    return pids


def _window_rect(pids):
    """(left, top, right, bottom) of the window owned by *pids* (platform gate)."""
    best = None
    for pid in pids or ():
        for info in windows_for_pid(int(pid)):
            rect = info.get('rect')
            if not rect:
                continue
            left, top, right, bottom = rect
            if right - left < 200 or bottom - top < 120:
                continue
            best = (left, top, right, bottom)
            break
        if best:
            break
    return best


def _window_title(pids):
    """Title of the window owned by *pids* (platform gate), or ''."""
    wanted = {int(p) for p in (pids or ())}
    for info in list_windows():
        if info.get('pid') in wanted and info.get('title'):
            return str(info['title'])
    return ''


def _grab(rect, out_path, max_width=1280):
    """Screenshot *rect* (left, top, right, bottom) to *out_path*; False on failure."""
    try:
        left, top, right, bottom = (int(v) for v in rect)
        img = capture_screen((left, top, right - left, bottom - top))
        if img.width > max_width:
            img = img.resize(
                (max_width, int(img.height * max_width / img.width)))
        img.convert('RGB').save(out_path, 'PNG')
        return True
    except Exception as e:
        logger.warning('GUI check: screenshot failed: %s', e)
        return False


def vision_model():
    """The vision GGUF, relative to the models dir (the gateway resolves it)."""
    try:
        from AI.model_downloads import MODELS
        for entry in MODELS:
            if entry.get('model_id') == 'lfm_vision':
                return entry.get('save_path') or VISION_MODEL
    except Exception:
        pass
    return VISION_MODEL


def _ask_vision(api_url, image_path, title, model=''):
    """The vision model's reading of the screenshot, or '' when unusable."""
    try:
        import requests
    except Exception:
        return ''
    prompt = ('App window title: ' + (title or '(untitled)') +
              '\nReview this screenshot.')
    payload = {
        'model': model or vision_model(),
        'prompt': prompt,
        'system': _SYSTEM,
        'temperature': 0.1,
        'max_tokens': 200,
        'stream': False,
        'use_llamacpp': True,
        'gpu_layers': 0,
        'threads': -1,
        'context_size': 0,
        'images': [image_path],       # the gateway encodes paths for us
    }
    try:
        # No timeout: local vision inference on a big screenshot can take
        # minutes, and a cut call would be reported as a broken window.
        resp = requests.post(f'{api_url.rstrip("/")}/llamacpp/generate',
                             json=payload, timeout=None)
        if resp.status_code != 200:
            logger.warning('GUI check: vision API %s: %s',
                           resp.status_code, resp.text[:200])
            return ''
        return str((resp.json() or {}).get('response') or '').strip()
    except Exception as e:
        logger.warning('GUI check: vision call failed: %s', e)
        return ''


def _kill(proc):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _verdict(text):
    """True when the vision model said the window looks OK."""
    return 'VERDICT: OK' in str(text or '').upper().replace(
        'VERDICT:OK', 'VERDICT: OK')


def _tail(text, limit=1200):
    text = str(text or '').strip()
    return text if len(text) <= limit else '...' + text[-limit:]


def run_gui_check(script_path, python_exe, api_url='', wait_s=_WAIT_S,
                  model=''):
    """Launch the script, screenshot its window, describe it.  Returns a dict:

    ``ok`` (the vision verdict), ``report`` (text for the agent),
    ``title``, ``stderr`` (traceback when it died before showing a window).
    The app is always killed before returning.
    """
    script_path = os.path.abspath(script_path)
    work_dir = os.path.dirname(script_path) or os.getcwd()
    env = dict(os.environ)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        proc = subprocess.Popen(
            [python_exe, script_path], cwd=work_dir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors='replace', creationflags=creation)
    except Exception as e:
        return {'ok': False, 'report': f'Could not start the script: {e}',
                'title': '', 'stderr': str(e)}

    shot = os.path.join(tempfile.gettempdir(), 'code_node_gui.png')
    try:
        rect = None
        pids = _pids_of(proc.pid)
        deadline = time.time() + max(wait_s, 1.0)
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            pids = _pids_of(proc.pid)
            rect = _window_rect(pids)
            if rect:
                break
            time.sleep(0.25)

        if proc.poll() is not None:
            err = ''
            try:
                _out, err = proc.communicate(timeout=5)
            except Exception:
                pass
            err = _tail(err) or f'exited with code {proc.returncode}'
            return {'ok': False,
                    'report': 'The app exited before opening a window:\n' + err,
                    'title': '', 'stderr': err}

        if not rect:
            return {'ok': False,
                    'report': f'No window appeared within {wait_s:.0f} s '
                              '(the process is still running).',
                    'title': '', 'stderr': ''}

        time.sleep(_SETTLE_S)
        title = _window_title(pids)
        if not _grab(rect, shot):
            return {'ok': False,
                    'report': 'The window opened but the screenshot failed.',
                    'title': title, 'stderr': ''}
        text = _ask_vision(api_url, shot, title, model=model) if api_url else ''
        if not text:
            return {'ok': False, 'title': title, 'stderr': '',
                    'report': 'The app opened a window titled "{}", but the '
                              'vision model could not read the screenshot, so '
                              'the screen is unverified.'.format(title)}
        verdict = _verdict(text)
        return {'ok': verdict,
                'report': text, 'title': title, 'stderr': ''}
    finally:
        _kill(proc)
        try:
            if os.path.exists(shot):
                os.remove(shot)
        except Exception:
            pass
