"""
Piper TTS Engine - shared TTS backend for LoOper.

Replaces pyttsx3/espeak with Piper neural TTS for more natural-sounding
speech synthesis.  Piper uses lightweight ONNX models and runs entirely
on CPU, making it fast and suitable for frozen (PyInstaller) builds.

Voice models (.onnx) are expected in ``LoOper/data/piper_voices/``.
They can be placed there manually or auto-downloaded from Hugging Face.
"""

import os
import sys
import wave
import tempfile
import threading
import time
import logging

logger = logging.getLogger("piper_tts")

# Suppress piper's internal debug logging which outputs IPA phonemes
# containing Unicode characters that Windows terminals often cannot display.
logging.getLogger("piper").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Default voices directory
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    # ponytail: in frozen builds, voice models live under data/ relative to _MEIPASS
    _PIPER_VOICES_DIR = os.path.join(sys._MEIPASS, 'data', 'piper_voices')
else:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    _PIPER_VOICES_DIR = os.path.normpath(
        os.path.join(_THIS_DIR, "..", "data", "piper_voices")
    )

# Popular starter voices keyed by language code
DEFAULT_VOICES = {
    "en": "en_US-lessac-medium",
    "es": "es_ES-carlfm-x_low",
    "fr": "fr_FR-upmc-medium",
    "de": "de_DE-thorsten-medium",
    "it": "it_IT-riccardo-x_low",
    "pt": "pt_BR-faber-medium",
    "ru": "ru_RU-irinia-medium",
    "ja": "ja_JP-kokoro-medium",
    "ko": "ko_KO-kss-medium",
    "zh": "zh_CN-huayan-medium",
}

# ---------------------------------------------------------------------------
# Voice cache (avoid reloading the same .onnx every call)
# ---------------------------------------------------------------------------
_voice_cache: dict = {}


def get_voices_dir() -> str:
    """Return the directory used to store Piper voice models, creating it if needed."""
    os.makedirs(_PIPER_VOICES_DIR, exist_ok=True)
    return _PIPER_VOICES_DIR


def list_available_voices() -> list:
    """Return ``.onnx`` filenames found in the voices directory."""
    d = get_voices_dir()
    return sorted(
        f for f in os.listdir(d) if f.lower().endswith(".onnx")
    )


def resolve_voice_path(voice_model: str = "", language: str = "en") -> str | None:
    """Resolve a voice model path.

    If *voice_model* is given and exists, it is returned directly (absolute or
    relative to the voices dir).  Otherwise the function looks for a file whose
    name starts with the *language* code (e.g. ``es_`` for ``es``).  Returns
    ``None`` when no matching voice is found so that the caller can trigger
    an auto-download.
    """
    voices_dir = get_voices_dir()

    # Explicit path / name
    if voice_model:
        if os.path.isabs(voice_model) and os.path.isfile(voice_model):
            return voice_model
        candidate = os.path.join(voices_dir, voice_model)
        if os.path.isfile(candidate):
            return candidate
        # Maybe caller passed just the basename without .onnx
        if not candidate.lower().endswith(".onnx"):
            candidate += ".onnx"
            if os.path.isfile(candidate):
                return candidate
        return None

    # Try language prefix match (e.g. "es" matches "es_ES-carlfm-x_low.onnx",
    # "en" matches "en_US-lessac-medium.onnx")
    lang_prefix = language.lower() + "_"
    for f in list_available_voices():
        if f.lower().startswith(lang_prefix) or f.lower().startswith(language.lower() + "-"):
            return os.path.join(voices_dir, f)

    # No matching voice found — return None so caller can auto-download
    return None


# ---------------------------------------------------------------------------
# Voice loading
# ---------------------------------------------------------------------------

def _load_voice(model_path: str):
    """Load (and cache) a :class:`piper.PiperVoice`."""
    if model_path in _voice_cache:
        return _voice_cache[model_path]
    from piper import PiperVoice
    voice = PiperVoice.load(model_path)
    _voice_cache[model_path] = voice
    return voice


# ---------------------------------------------------------------------------
# Auto-download
# ---------------------------------------------------------------------------

def download_voice(voice_name: str, voices_dir: str | None = None) -> str:
    """Download a Piper voice model from Hugging Face.

    The voice name must follow the Piper naming convention, e.g.:
    ``en_US-lessac-medium``  →  ``en/en_US/lessac/medium/en_US-lessac-medium.onnx``

    Returns the local path to the ``.onnx`` file.
    """
    import urllib.request

    voices_dir = voices_dir or get_voices_dir()
    os.makedirs(voices_dir, exist_ok=True)

    onnx_name = f"{voice_name}.onnx"
    onnx_path = os.path.join(voices_dir, onnx_name)
    json_path = os.path.join(voices_dir, f"{voice_name}.onnx.json")

    # Parse voice name: e.g. "en_US-lessac-medium"
    #   lang      = "en"     (first 2 chars before '_')
    #   locale    = "en_US"  (everything before the first '-')
    #   voice     = "lessac" (between first '-' and last '-')
    #   quality   = "medium" (after the last '-')
    locale = voice_name.split("-")[0]            # "en_US"
    lang = locale.split("_")[0]                  # "en"
    quality = voice_name.rsplit("-", 1)[-1]      # "medium"
    # voice part is between locale and quality
    voice = voice_name[len(locale) + 1 : -(len(quality) + 1)]  # "lessac"

    base_url = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
        f"/{lang}/{locale}/{voice}/{quality}/{onnx_name}"
    )

    logger.info(f"Downloading Piper voice model: {voice_name}")
    logger.info(f"URL: {base_url}")

    urllib.request.urlretrieve(base_url, onnx_path)
    logger.info(f"Downloaded model to {onnx_path}")

    # Companion JSON config (non-fatal if missing)
    try:
        json_url = base_url + ".json"
        urllib.request.urlretrieve(json_url, json_path)
        logger.info(f"Downloaded config to {json_path}")
    except Exception as e:
        logger.warning(f"Could not download voice config: {e}")

    return onnx_path


# ---------------------------------------------------------------------------
# Core synthesis
# ---------------------------------------------------------------------------

def synthesize(
    text: str,
    output_path: str | None = None,
    voice_model: str = "",
    language: str = "en",
    length_scale: float = 1.0,
    speaker_id: int | None = None,
    auto_download: bool = True,
) -> dict:
    """Synthesize *text* to a WAV file using Piper.

    Parameters
    ----------
    text : str
        Text to speak.
    output_path : str or None
        Destination WAV path.  A temp file is created when *None*.
    voice_model : str
        Path to a ``.onnx`` model, or just the model name (resolved against
        the voices directory).  Empty → auto-resolved from *language*.
    language : str
        ISO language code used to auto-select a voice model.
    length_scale : float
        Speed factor.  ``1.0`` = normal, ``<1`` = faster, ``>1`` = slower.
    speaker_id : int or None
        Multi-speaker voice speaker index.  *None* = default speaker.
    auto_download : bool
        Download the default voice for *language* if no model is found.

    Returns
    -------
    dict
        ``{success, output_path, sample_rate, message}`` or ``{error}``.
    """
    try:
        # -- resolve voice model ------------------------------------------
        model_path = resolve_voice_path(voice_model, language)

        if model_path is None and auto_download:
            default_voice = DEFAULT_VOICES.get(
                language, DEFAULT_VOICES.get("en", "en_US-lessac-medium")
            )
            logger.info(
                f"No voice model found for '{language}'. "
                f"Auto-downloading '{default_voice}'…"
            )
            try:
                model_path = download_voice(default_voice)
            except Exception as e:
                return {
                    "error": (
                        f"Could not download default Piper voice "
                        f"'{default_voice}': {e}\n"
                        f"Place a .onnx file in: {get_voices_dir()}"
                    )
                }

        if model_path is None:
            return {
                "error": (
                    f"No Piper voice model found for language '{language}'. "
                    f"Place a .onnx file in: {get_voices_dir()}"
                )
            }

        # -- load voice ---------------------------------------------------
        voice = _load_voice(model_path)

        # -- determine output path ----------------------------------------
        is_temp = output_path is None or output_path == ""
        if is_temp:
            fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="piper_tts_")
            os.close(fd)

        # -- synthesize ---------------------------------------------------
        sample_rate = voice.config.sample_rate
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit PCM
            wav_file.setframerate(sample_rate)

            from piper import SynthesisConfig
            syn_config = SynthesisConfig(
                length_scale=length_scale,
                speaker_id=speaker_id,
            )
            for audio_chunk in voice.synthesize(text, syn_config=syn_config):
                wav_file.writeframes(audio_chunk.audio_int16_bytes)

        msg = (
            f"Piper TTS: synthesized '{text[:60]}…'"
            if len(text) > 60
            else f"Piper TTS: synthesized '{text}'"
        )
        return {
            "success": True,
            "output_path": output_path,
            "sample_rate": sample_rate,
            "message": msg,
            "_is_temp": is_temp,
        }

    except ImportError:
        return {
            "error": (
                "piper-tts is not installed. "
                "Install with: pip install piper-tts"
            )
        }
    except Exception as e:
        logger.exception("Piper synthesis failed")
        return {"error": f"Piper TTS synthesis failed: {e}"}


# ---------------------------------------------------------------------------
# Playback helpers
# ---------------------------------------------------------------------------

def _wav_duration(wav_path: str) -> float | None:
    """Return the duration of a WAV file in seconds, or None on failure."""
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else None
    except Exception as e:
        logger.debug(f"Could not determine WAV duration: {e}")
        return None


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def play_wav(wav_path: str, wait: bool = True, stop_event: threading.Event | None = None):
    """Play a WAV file.

    On Windows the built-in ``winsound`` module is used.  Playback always
    uses ``SND_ASYNC`` internally so that it can be reliably interrupted by
    calling ``winsound.PlaySound(None, SND_PURGE)`` (triggered by the ESC
    key via the keyboard monitor).  When *wait* is True the function blocks
    the caller by polling until the sound finishes or *stop_event* is set.
    """
    if sys.platform == "win32":
        import winsound

        # Always start playback asynchronously so we can stop it reliably.
        winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)

        if not wait:
            return

        # --- simulate synchronous wait, honouring stop_event -----------
        # Get WAV duration from the file header so we know when to stop.
        duration = _wav_duration(wav_path)
        if duration is None:
            # Fallback: just block on a generous timeout
            duration = 600.0  # 10 min safety cap

        deadline = time.monotonic() + duration
        poll_interval = 0.05  # 50 ms

        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                winsound.PlaySound(None, winsound.SND_PURGE)  # stop async sound immediately
                logger.info("TTS playback interrupted by stop event")
                return
            time.sleep(poll_interval)

    else:
        # Linux / macOS fallback
        import subprocess
        proc = subprocess.Popen(
            ["aplay", wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait:
            return
        # Poll so we can honour stop_event
        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                proc.terminate()
                proc.wait(timeout=2)
                logger.info("TTS playback interrupted by stop event")
                return
            time.sleep(0.05)
