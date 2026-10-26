"""
Dual Neural Text-to-Speech (TTS) Service
- Kokoro-82M ONNX for English & International voices (Studio quality)
- Piper Neural ONNX for Native Russian Audiobook voices (Ирина, Дмитрий, Руслан)
Includes automatic language detection, text sanitization, and deterministic MP3 caching.
"""

import hashlib
import io
import logging
import os
import re
import subprocess
import threading
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

# Base paths (supports both /opt/bookserver/api/app and standalone)
BASE_DIR = Path(__file__).resolve().parent.parent

POSSIBLE_DIRS = [
    BASE_DIR / "models",
    BASE_DIR.parent / "models",
    Path("/opt/bookserver/api/models")
]

MODELS_DIR = next((d for d in POSSIBLE_DIRS if d.is_dir()), BASE_DIR / "models")
KOKORO_DIR = MODELS_DIR / "kokoro"
PIPER_DIR = MODELS_DIR / "piper"

KOKORO_MODEL_PATH = KOKORO_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = KOKORO_DIR / "voices-v1.0.bin"

CACHE_DIR = BASE_DIR / "cache" / "tts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Curated voice catalog for audiobook narration
VOICE_CATALOG = [
    # --- Native Russian Voices (Piper Neural) ---
    {"id": "ru_irina", "name": "🇷🇺 Ирина (RU Female, Natural & Expressive)", "gender": "female", "lang": "ru", "engine": "piper", "starred": True},
    {"id": "ru_dmitri", "name": "🇷🇺 Дмитрий (RU Male, Classic Narrator)", "gender": "male", "lang": "ru", "engine": "piper", "starred": True},
    {"id": "ru_ruslan", "name": "🇷🇺 Руслан (RU Male, Rich Storyteller)", "gender": "male", "lang": "ru", "engine": "piper", "starred": False},

    # --- American Voices (Kokoro-82M) ---
    {"id": "af_heart", "name": "🇺🇸 Heart (US Female, Warm & Expressive)", "gender": "female", "lang": "en-us", "engine": "kokoro", "starred": True},
    {"id": "af_bella", "name": "🇺🇸 Bella (US Female, Clear Narrator)", "gender": "female", "lang": "en-us", "engine": "kokoro", "starred": True},
    {"id": "af_sarah", "name": "🇺🇸 Sarah (US Female, Soft & Calm)", "gender": "female", "lang": "en-us", "engine": "kokoro", "starred": False},
    {"id": "af_nicole", "name": "🇺🇸 Nicole (US Female, Confident)", "gender": "female", "lang": "en-us", "engine": "kokoro", "starred": False},
    {"id": "af_sky", "name": "🇺🇸 Sky (US Female, Bright)", "gender": "female", "lang": "en-us", "engine": "kokoro", "starred": False},
    {"id": "am_adam", "name": "🇺🇸 Adam (US Male, Deep & Steady)", "gender": "male", "lang": "en-us", "engine": "kokoro", "starred": True},
    {"id": "am_michael", "name": "🇺🇸 Michael (US Male, Rich Audiobook)", "gender": "male", "lang": "en-us", "engine": "kokoro", "starred": True},
    {"id": "am_fenrir", "name": "🇺🇸 Fenrir (US Male, Resonant)", "gender": "male", "lang": "en-us", "engine": "kokoro", "starred": False},
    {"id": "am_puck", "name": "🇺🇸 Puck (US Male, Energetic)", "gender": "male", "lang": "en-us", "engine": "kokoro", "starred": False},

    # --- British Voices (Kokoro-82M) ---
    {"id": "bf_emma", "name": "🇬🇧 Emma (UK Female, Refined)", "gender": "female", "lang": "en-gb", "engine": "kokoro", "starred": True},
    {"id": "bf_isabella", "name": "🇬🇧 Isabella (UK Female, Storyteller)", "gender": "female", "lang": "en-gb", "engine": "kokoro", "starred": False},
    {"id": "bm_george", "name": "🇬🇧 George (UK Male, Classic BBC Novel)", "gender": "male", "lang": "en-gb", "engine": "kokoro", "starred": True},
    {"id": "bm_lewis", "name": "🇬🇧 Lewis (UK Male, Warm Narrative)", "gender": "male", "lang": "en-gb", "engine": "kokoro", "starred": False},

    # --- Other European Languages (Kokoro-82M) ---
    {"id": "ef_dora", "name": "🇪🇸 Dora (Spanish Female)", "gender": "female", "lang": "es", "engine": "kokoro", "starred": False},
    {"id": "em_alex", "name": "🇪🇸 Alex (Spanish Male)", "gender": "male", "lang": "es", "engine": "kokoro", "starred": False},
    {"id": "ff_siwis", "name": "🇫🇷 Siwis (French Female)", "gender": "female", "lang": "fr-fr", "engine": "kokoro", "starred": False},
    {"id": "if_sara", "name": "🇮🇹 Sara (Italian Female)", "gender": "female", "lang": "it", "engine": "kokoro", "starred": False},
]

DEFAULT_VOICE = "af_heart"
DEFAULT_RUSSIAN_VOICE = "ru_irina"

_kokoro_instance = None
_kokoro_lock = threading.Lock()
_piper_voices = {}
_piper_lock = threading.Lock()


def get_kokoro():
    """Singleton getter for the Kokoro model instance."""
    global _kokoro_instance
    if _kokoro_instance is None:
        with _kokoro_lock:
            if _kokoro_instance is None:
                if not KOKORO_MODEL_PATH.is_file() or not KOKORO_VOICES_PATH.is_file():
                    raise FileNotFoundError(
                        f"Kokoro model files missing: {KOKORO_MODEL_PATH}"
                    )
                logger.info("Initializing Kokoro ONNX model...")
                from kokoro_onnx import Kokoro
                _kokoro_instance = Kokoro(
                    model_path=str(KOKORO_MODEL_PATH),
                    voices_path=str(KOKORO_VOICES_PATH)
                )
    return _kokoro_instance


def get_piper_voice(voice_id: str):
    """Cached loader for Piper ONNX voices."""
    with _piper_lock:
        if voice_id in _piper_voices:
            return _piper_voices[voice_id]

        model_file = PIPER_DIR / f"{voice_id}.onnx"
        if not model_file.is_file():
            # Try alternate naming
            model_file = PIPER_DIR / f"{voice_id.replace('ru_', 'ru_RU-')}-medium.onnx"
            if not model_file.is_file():
                raise FileNotFoundError(f"Piper model not found: {voice_id} at {PIPER_DIR}")

        from piper import PiperVoice
        voice = PiperVoice.load(str(model_file))
        _piper_voices[voice_id] = voice
        return voice


def is_cyrillic(text: str) -> bool:
    """Check if text contains Cyrillic characters."""
    return bool(re.search(r'[\u0400-\u04FF]', text))


def sanitize_text_for_speech(text: str) -> str:
    """
    Cleans raw ebook text into natural, spoken narration:
    - Removes HTML tags and XML entities
    - Removes footnote references like [1], [23], (1)
    - Replaces brackets and dashes with natural speaking pauses
    - Normalizes quotes, whitespace, and abbreviations
    """
    if not text:
        return ""

    # Strip HTML tags
    clean = re.sub(r'<[^>]+>', ' ', text)

    # Decode common HTML entities
    clean = clean.replace('&nbsp;', ' ').replace('&mdash;', ' — ').replace('&ndash;', ' — ')
    clean = clean.replace('&hellip;', '...').replace('&quot;', '"').replace('&apos;', "'")
    clean = clean.replace('&amp;', '&')

    # Remove footnote markers like [1], [12], [*]
    clean = re.sub(r'\[\s*\d+\s*\]', '', clean)
    clean = re.sub(r'\[\s*[\*\#\†\‡]\s*\]', '', clean)

    # Standardize ellipsis to ensure pause
    clean = re.sub(r'\.{3,}', '... ', clean)

    # Standardize em dashes to introduce natural pauses
    clean = re.sub(r'[\u2014\u2013\u2015]', ' — ', clean)

    # English abbreviation expansions
    clean = re.sub(r'\bMr\.\s*', 'Mister ', clean)
    clean = re.sub(r'\bMrs\.\s*', 'Missus ', clean)
    clean = re.sub(r'\bMs\.\s*', 'Mizz ', clean)
    clean = re.sub(r'\bDr\.\s*', 'Doctor ', clean)
    clean = re.sub(r'\bProf\.\s*', 'Professor ', clean)
    clean = re.sub(r'\bSt\.\s*', 'Saint ', clean)
    clean = re.sub(r'\betc\.', 'etcetera', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\be\.g\.', 'for example', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\bi\.e\.', 'that is', clean, flags=re.IGNORECASE)

    # Russian abbreviation expansions
    clean = re.sub(r'\bг-н\b', 'господин', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\bг-жа\b', 'госпожа', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\bт\.д\.', 'так далее', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\bт\.п\.', 'тому подобное', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\bт\.е\.', 'то есть', clean, flags=re.IGNORECASE)

    # Remove remaining markdown link syntax [text](url) -> text
    clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean)

    # Collapse multiple whitespaces
    clean = re.sub(r'\s+', ' ', clean).strip()

    return clean


def synthesize_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    lang: str | None = None
) -> dict:
    """
    Synthesizes speech using the optimal neural engine:
    - Piper for Native Russian voices
    - Kokoro-82M for English & European voices
    """
    clean_text = sanitize_text_for_speech(text)
    if not clean_text:
        return {"error": "Empty text provided"}

    # Clamp speed between 0.5x and 2.0x
    speed = max(0.5, min(2.0, float(speed)))

    # Language and voice auto-resolution
    text_has_cyrillic = is_cyrillic(clean_text)

    valid_voice = voice if any(v["id"] == voice for v in VOICE_CATALOG) else DEFAULT_VOICE
    selected_voice_meta = next((v for v in VOICE_CATALOG if v["id"] == valid_voice), None)

    # If text is in Russian but an English voice was selected, auto-switch to Russian voice
    if text_has_cyrillic and selected_voice_meta and selected_voice_meta.get("engine") != "piper":
        # Keep same gender if possible
        if selected_voice_meta.get("gender") == "male":
            valid_voice = "ru_dmitri"
        else:
            valid_voice = "ru_irina"
        selected_voice_meta = next((v for v in VOICE_CATALOG if v["id"] == valid_voice), None)

    engine = selected_voice_meta.get("engine", "kokoro") if selected_voice_meta else "kokoro"
    resolved_lang = selected_voice_meta.get("lang", "en-us") if selected_voice_meta else "en-us"

    # Compute deterministic cache key
    cache_payload = f"{clean_text}|{valid_voice}|{speed:.2f}|{engine}".encode("utf-8")
    cache_hash = hashlib.sha256(cache_payload).hexdigest()
    cache_filename = f"{cache_hash}.mp3"
    cache_file = CACHE_DIR / cache_filename

    # If already cached, return immediately
    if cache_file.is_file() and cache_file.stat().st_size > 0:
        return {
            "cached": True,
            "filename": cache_filename,
            "url": f"/api/tts/audio/{cache_filename}",
            "text": clean_text,
            "voice": valid_voice,
            "speed": speed,
            "lang": resolved_lang,
            "engine": engine
        }

    temp_cache_file = CACHE_DIR / f"{cache_hash}.tmp.mp3"

    # --- PIPER SYNTHESIS (NATIVE RUSSIAN) ---
    if engine == "piper":
        piper_voice = get_piper_voice(valid_voice)
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            piper_voice.synthesize_wav(clean_text, wf)
        wav_bytes = wav_io.getvalue()

        # Convert WAV to MP3 via ffmpeg
        cmd = [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-b:a", "96k",
            "-f", "mp3",
            str(temp_cache_file)
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        proc.communicate(input=wav_bytes)

        if proc.returncode != 0 or not temp_cache_file.is_file():
            raise RuntimeError("ffmpeg failed to encode Piper WAV to MP3")

        # Estimate duration
        sample_rate = piper_voice.config.sample_rate or 22050
        duration = len(wav_bytes) / (sample_rate * 2)  # 16-bit mono

    # --- KOKORO SYNTHESIS (ENGLISH & EUROPEAN) ---
    else:
        kokoro = get_kokoro()
        with _kokoro_lock:
            samples, sample_rate = kokoro.create(
                clean_text,
                voice=valid_voice,
                speed=speed,
                lang=resolved_lang
            )

        import numpy as np
        raw_pcm = samples.astype(np.float32).tobytes()

        cmd = [
            "ffmpeg", "-y",
            "-f", "f32le",
            "-ar", str(sample_rate),
            "-ac", "1",
            "-i", "pipe:0",
            "-b:a", "96k",
            "-f", "mp3",
            str(temp_cache_file)
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        proc.communicate(input=raw_pcm)

        if proc.returncode != 0 or not temp_cache_file.is_file():
            raise RuntimeError("ffmpeg failed to encode Kokoro audio to MP3")

        duration = len(samples) / sample_rate

    temp_cache_file.replace(cache_file)

    return {
        "cached": False,
        "filename": cache_filename,
        "url": f"/api/tts/audio/{cache_filename}",
        "duration": round(duration, 2),
        "text": clean_text,
        "voice": valid_voice,
        "speed": speed,
        "lang": resolved_lang,
        "engine": engine
    }
