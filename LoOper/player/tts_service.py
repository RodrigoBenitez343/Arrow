"""Non-blocking TTS service for Output node speech.

A single daemon worker thread consumes a FIFO queue, synthesizing each
job (with a session cache so repeated text is never re-synthesized) and
playing the WAV in order.  Callers ``submit()`` a job and, when
``wait=True``, block on the returned event while polling stop sources so
ESC stays responsive.  The engine (tts_engine.py) is untouched.
"""

import atexit
import logging
import os
import queue
import shutil
import tempfile
import threading

from .tts_engine import synthesize, play_wav

logger = logging.getLogger(__name__)


class TTSService:
    """FIFO worker thread + per-session synthesis cache."""

    def __init__(self):
        self._queue = queue.Queue()
        self._cache = {}
        self._cache_dir = tempfile.mkdtemp(prefix="looper_tts_")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        atexit.register(self._cleanup)

    # -- public API ------------------------------------------------------

    def submit(self, text, voice_model="", language="en", speed=1.0,
               speaker_id=None, wait=False):
        """Queue a speech job; returns a ``threading.Event`` set when done.

        With ``wait=True`` the caller blocks on the event (polling the
        keyboard stop_event) so the workflow pauses while speaking; with
        ``wait=False`` the workflow continues and speech plays in FIFO order.
        """
        done = threading.Event()
        self._queue.put({
            "text": text,
            "voice_model": voice_model,
            "language": language,
            "speed": speed,
            "speaker_id": speaker_id,
            "done": done,
        })
        if not wait:
            return done
        try:
            from .keyboard_monitor import get_keyboard_monitor
            stop_event = get_keyboard_monitor().stop_event
        except Exception:
            stop_event = None
        # Poll so ESC (via stop_event) aborts the wait without killing the
        # worker thread's current playback.
        while not done.is_set():
            if stop_event is not None and stop_event.is_set():
                break
            done.wait(0.05)
        return done

    # -- worker ----------------------------------------------------------

    def _run(self):
        while True:
            job = self._queue.get()
            try:
                self._play_job(job)
            except Exception as e:
                logger.warning("TTS job failed: %s", e)
            finally:
                job.get("done", threading.Event()).set()

    def _play_job(self, job):
        key = (job["text"], job["voice_model"], job["language"],
               job["speed"], job["speaker_id"])
        wav = self._cache.get(key)
        if wav is None or not os.path.isfile(wav):
            result = synthesize(
                text=job["text"],
                voice_model=job["voice_model"],
                language=job["language"],
                length_scale=job["speed"],
                speaker_id=job["speaker_id"],
                output_path=os.path.join(self._cache_dir, f"{len(self._cache)}.wav"),
                auto_download=True,
            )
            if not result.get("success"):
                logger.warning("TTS synthesis failed: %s",
                               result.get("error", "unknown error"))
                return
            wav = result["output_path"]
            self._cache[key] = wav
        try:
            from .keyboard_monitor import get_keyboard_monitor
            stop_event = get_keyboard_monitor().stop_event
        except Exception:
            stop_event = None
        # Blocking wait inside the worker keeps FIFO order: the next job
        # only starts after this speech finishes (or ESC interrupts it).
        play_wav(wav, wait=True, stop_event=stop_event)

    def _cleanup(self):
        shutil.rmtree(self._cache_dir, ignore_errors=True)


_service = None
_lock = threading.Lock()


def submit(text, voice_model="", language="en", speed=1.0, speaker_id=None,
           wait=False):
    """Module-level convenience: submit a speech job to the shared service."""
    global _service
    with _lock:
        if _service is None:
            _service = TTSService()
    return _service.submit(text, voice_model=voice_model, language=language,
                           speed=speed, speaker_id=speaker_id, wait=wait)
