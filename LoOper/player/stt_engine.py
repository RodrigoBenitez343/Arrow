"""
Vosk STT Engine - offline speech-to-text backend for LoOper.

Uses Vosk (Kaldi-based) for real-time speech recognition entirely
on-device.  Model files are expected in ``LoOper/data/vosk_models/``
and can be placed manually or auto-downloaded from Vosk's model server.
"""

import json
import logging
import os
import sys
import threading

logger = logging.getLogger("vosk_stt")

# Suppress Vosk's internal debug logging
logging.getLogger("vosk").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Default models directory
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Candidate locations for the bundled Vosk models.  In the source tree the
# models live at LoOper/data/vosk_models (module-relative ``../data``).  In a
# frozen build the engine can be imported as top-level ``player.stt_engine``
# (__file__ = _internal/player/...) or as ``LoOper.player.stt_engine``
# (__file__ = _internal/LoOper/player/...), and the data can sit under
# _MEIPASS or next to the exe - so scan every plausible root and use the
# first directory that actually holds a model.
_VOSK_MODELS_CANDIDATES = [
    os.path.normpath(os.path.join(_THIS_DIR, "..", "data", "vosk_models")),
]
_meipass = getattr(sys, "_MEIPASS", None)
if _meipass:
    _VOSK_MODELS_CANDIDATES += [
        os.path.join(_meipass, "data", "vosk_models"),
        os.path.join(_meipass, "LoOper", "data", "vosk_models"),
    ]
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    _VOSK_MODELS_CANDIDATES += [
        os.path.join(_exe_dir, "data", "vosk_models"),
        os.path.join(_exe_dir, "_internal", "data", "vosk_models"),
    ]

# Default small model for English (fast, ~40 MB)
DEFAULT_MODEL_NAME = "vosk-model-small-en-us-0.15"

# ---------------------------------------------------------------------------
# Model cache (avoid reloading across requests)
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()
_model_cache: dict = {}


def get_models_dir() -> str:
    """Return the directory that actually holds the Vosk models.

    The first candidate containing a model wins (the bundled copy in frozen
    builds, wherever PyInstaller laid it out); when none exists yet, the
    module-relative location is created so ``download_model()`` has a
    writable target.
    """
    for cand in _VOSK_MODELS_CANDIDATES:
        try:
            if any(
                os.path.isdir(os.path.join(cand, name))
                for name in os.listdir(cand)
            ):
                return cand
        except OSError:
            continue
    primary = _VOSK_MODELS_CANDIDATES[0]
    os.makedirs(primary, exist_ok=True)
    return primary


def list_available_models() -> list:
    """Return model directory names found in the models directory."""
    d = get_models_dir()
    try:
        return sorted(
            name for name in os.listdir(d)
            if os.path.isdir(os.path.join(d, name))
        )
    except Exception:
        return []


def resolve_model_path(model_name: str = "") -> str | None:
    """Resolve a Vosk model path.

    If *model_name* is given, looks for a matching directory in the
    models folder.  Otherwise returns the first available model dir
    or None.
    """
    models_dir = get_models_dir()

    if model_name:
        candidate = os.path.join(models_dir, model_name)
        if os.path.isdir(candidate):
            return candidate
        return None

    # Pick the first available model
    available = list_available_models()
    if available:
        return os.path.join(models_dir, available[0])
    return None


def download_model(model_name: str = DEFAULT_MODEL_NAME) -> str:
    """Download a Vosk model from the official Vosk model server.

    Uses Vosk's built-in download utilities.  Returns the local path
    to the model directory.
    """
    from vosk import MODEL_PRE_URL

    models_dir = get_models_dir()
    model_dir = os.path.join(models_dir, model_name)

    if os.path.isdir(model_dir):
        logger.info("Vosk model '%s' already exists at %s", model_name, model_dir)
        return model_dir

    logger.info("Downloading Vosk model '%s' ...", model_name)
    zip_path = os.path.join(models_dir, f"{model_name}.zip")

    try:
        import urllib.request
        url = f"{MODEL_PRE_URL}/{model_name}.zip"
        logger.info("Download URL: %s", url)
        urllib.request.urlretrieve(url, zip_path)
        logger.info("Downloaded %s, extracting ...", zip_path)

        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(models_dir)

        os.remove(zip_path)
        logger.info("Vosk model '%s' ready at %s", model_name, model_dir)
    except Exception as e:
        logger.error("Failed to download Vosk model '%s': %s", model_name, e)
        # Clean up partial download
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise

    return model_dir


class SttEngine:
    """Lazy-loaded Vosk speech recognition engine.

    Thread-safe: the underlying Vosk ``Model`` and ``KaldiRecognizer``
    are created once and reused.  Each ``transcribe()`` call creates a
    fresh recognizer internally since Vosk recognizers are not reusable
    after a final result.
    """

    def __init__(self, model_name: str = ""):
        self._model_name = model_name or DEFAULT_MODEL_NAME
        self._model_path: str | None = None
        self._model = None  # vosk.Model (lazy)
        self._sample_rate: int = 16000

    def _ensure_model(self):
        """Lazy-load the Vosk model."""
        if self._model is not None:
            return True

        with _model_lock:
            if self._model is not None:
                return True

            # Check cache first
            cache_key = self._model_name or DEFAULT_MODEL_NAME
            if cache_key in _model_cache:
                self._model = _model_cache[cache_key]
                self._model_path = resolve_model_path(cache_key)
                return True

            # Resolve or download model
            model_path = resolve_model_path(self._model_name)
            if model_path is None:
                try:
                    model_path = download_model(self._model_name)
                except Exception as e:
                    logger.error("Vosk model unavailable: %s", e)
                    return False

            logger.info("Loading Vosk model from %s ...", model_path)
            try:
                from vosk import Model
                self._model = Model(model_path)
                self._model_path = model_path
                _model_cache[cache_key] = self._model
                logger.info("Vosk model loaded successfully")
                return True
            except Exception as e:
                logger.error("Failed to load Vosk model: %s", e)
                return False

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> dict:
        """Transcribe PCM audio bytes to text.

        Args:
            audio_bytes: Raw PCM 16-bit mono audio data.
            sample_rate: Sample rate of the audio (default 16000).

        Returns:
            dict with keys:
                - ``success`` (bool)
                - ``text`` (str) — transcribed text
                - ``partial`` (bool) — whether this is a partial result
                - ``message`` (str) — human-readable status
                - ``error`` (str, only on failure)
        """
        if not self._ensure_model():
            return {"error": "Vosk model not loaded"}

        try:
            from vosk import KaldiRecognizer

            rec = KaldiRecognizer(self._model, sample_rate)
            rec.SetWords(True)

            if rec.AcceptWaveform(audio_bytes):
                result = json.loads(rec.Result())
                text = result.get("text", "").strip()
                return {
                    "success": True,
                    "text": text,
                    "partial": False,
                    "message": f"Transcribed {len(text)} chars",
                }
            else:
                # Partial result or no speech detected
                partial = json.loads(rec.PartialResult())
                text = partial.get("partial", "").strip()
                if text:
                    return {
                        "success": True,
                        "text": text,
                        "partial": True,
                        "message": f"Partial transcription: {text[:60]}",
                    }
                return {
                    "success": False,
                    "text": "",
                    "partial": True,
                    "message": "No speech detected",
                }
        except Exception as e:
            logger.exception("Vosk transcription failed")
            return {"error": f"Vosk transcription failed: {e}"}

    def transcribe_file(self, wav_path: str) -> dict:
        """Transcribe a WAV file.

        Reads the WAV file, extracts PCM data and sample rate, then
        calls ``transcribe()``.
        """
        import wave
        try:
            with wave.open(wav_path, "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())

            # Convert to mono if needed
            if channels > 1:
                import array
                import struct
                if sampwidth == 2:
                    fmt = "<" + "h" * (channels)
                    mono = array.array("h", [0]) * (len(frames) // (sampwidth * channels))
                    idx = 0
                    for i in range(0, len(frames), sampwidth * channels):
                        chunk = struct.unpack_from(fmt, frames, i)
                        mono[idx] = chunk[0]  # Just take left channel
                        idx += 1
                    frames = mono.tobytes()
                else:
                    # Fallback: just use first channel raw bytes
                    mono_frames = bytearray()
                    for i in range(0, len(frames), sampwidth * channels):
                        mono_frames.extend(frames[i:i + sampwidth])
                    frames = bytes(mono_frames)

            return self.transcribe(frames, sample_rate)
        except Exception as e:
            logger.exception("Failed to transcribe WAV file")
            return {"error": f"WAV transcription failed: {e}"}


# ---------------------------------------------------------------------------
# Module-level convenience (single shared engine)
# ---------------------------------------------------------------------------
_engine_lock = threading.Lock()
_shared_engine: SttEngine | None = None


def get_stt_engine(model_name: str = "") -> SttEngine:
    """Get or create the shared STT engine instance."""
    global _shared_engine
    if _shared_engine is None:
        with _engine_lock:
            if _shared_engine is None:
                _shared_engine = SttEngine(model_name)
    return _shared_engine


def transcribe(audio_bytes: bytes, sample_rate: int = 16000) -> dict:
    """Convenience function: transcribe audio using the shared engine."""
    engine = get_stt_engine()
    return engine.transcribe(audio_bytes, sample_rate)


def transcribe_wav(wav_path: str) -> dict:
    """Convenience function: transcribe a WAV file using the shared engine."""
    engine = get_stt_engine()
    return engine.transcribe_file(wav_path)
