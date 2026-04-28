from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import base64
import math
import struct
import wave
import os

import requests


DEFAULT_ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
DEFAULT_ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")


@lru_cache(maxsize=1)
def build_minute_chime_wav_bytes() -> bytes:
    sample_rate = 22050
    amplitude = 0.24
    attack_seconds = 0.01
    decay_seconds = 0.34
    frequencies = (880.0, 1174.66)
    total_frames = int(sample_rate * (attack_seconds + decay_seconds))
    audio_buffer = BytesIO()

    with wave.open(audio_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        for frame_index in range(total_frames):
            timestamp = frame_index / sample_rate
            if timestamp <= attack_seconds:
                envelope = timestamp / max(attack_seconds, 1e-6)
            else:
                decay_progress = (timestamp - attack_seconds) / max(decay_seconds, 1e-6)
                envelope = max(0.0, 1.0 - decay_progress)

            sample_value = sum(
                math.sin(2.0 * math.pi * frequency * timestamp)
                for frequency in frequencies
            ) / len(frequencies)
            pcm_value = int(32767 * amplitude * envelope * sample_value)
            wav_file.writeframesraw(struct.pack("<h", pcm_value))

    return audio_buffer.getvalue()


def build_hidden_autoplay_audio_html(audio_bytes: bytes, mime_type: str) -> str:
    encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
    return (
        "<audio autoplay style='display:none'>"
        f"<source src='data:{mime_type};base64,{encoded_audio}' type='{mime_type}'>"
        "</audio>"
    )


def convert_text_to_speech(
    text: str,
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
    model_id: str | None = None,
    timeout_seconds: int = 45,
) -> tuple[bytes | None, str | None]:
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return None, "There is no message to read aloud."

    resolved_api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not resolved_api_key:
        return None, "ELEVENLABS_API_KEY is not configured, so voice playback is unavailable."

    resolved_voice_id = voice_id or DEFAULT_ELEVENLABS_VOICE_ID
    resolved_model_id = model_id or DEFAULT_ELEVENLABS_MODEL_ID
    endpoint = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{resolved_voice_id}"
        "?output_format=mp3_44100_128"
    )

    try:
        response = requests.post(
            endpoint,
            headers={
                "xi-api-key": resolved_api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": cleaned_text,
                "model_id": resolved_model_id,
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as error:
        return None, f"Voice playback could not reach ElevenLabs: {error}"

    if not response.ok:
        detail = response.text.strip()[:200] or response.reason
        return None, (
            "ElevenLabs could not generate the voice message right now. "
            f"Details: {detail}"
        )

    return response.content, None
