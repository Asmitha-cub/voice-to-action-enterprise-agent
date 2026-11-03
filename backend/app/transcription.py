"""Speech-to-text via Whisper on Groq (OpenAI-compatible endpoint).

Surfaces the real reason when transcription fails (bad key, wrong model,
unsupported audio) instead of a generic error, so problems are diagnosable.
"""
from io import BytesIO

from openai import OpenAI

from .config import settings


def transcribe_audio(filename: str, audio: bytes) -> str:
    cfg = settings()
    if not cfg.groq_api_key:
        raise ValueError('GROQ_API_KEY is not configured. Add it to backend/.env '
                         'and restart. Get a free key at https://console.groq.com.')
    if not audio:
        raise ValueError('No audio was received. Please record again.')

    # Whisper picks the decoder from the file extension, so ensure a valid one.
    if not filename or '.' not in filename:
        filename = 'voice-command.webm'

    client = OpenAI(api_key=cfg.groq_api_key, base_url=cfg.groq_base_url,
                    timeout=60, max_retries=1)
    file_object = BytesIO(audio)
    file_object.name = filename
    try:
        response = client.audio.transcriptions.create(
            model=cfg.whisper_model, file=file_object, response_format='json',
        )
    except Exception as exc:  # noqa: BLE001 - surface the real provider error
        msg = str(exc)
        low = msg.lower()
        if 'api key' in low or 'invalid_api_key' in low or '401' in low:
            raise ValueError('Speech-to-text failed: the Groq API key is invalid or '
                             'expired. Generate a new one at https://console.groq.com '
                             'and put it in GROQ_API_KEY.') from exc
        if 'model' in low and ('not found' in low or 'decommission' in low or 'does not exist' in low):
            raise ValueError(f'Speech-to-text failed: the model "{cfg.whisper_model}" is '
                             'not available. Try WHISPER_MODEL=whisper-large-v3 in .env.') from exc
        raise ValueError(f'Speech-to-text failed: {msg}') from exc

    text = (response.text or '').strip()
    if not text:
        raise ValueError('Speech-to-text returned an empty transcript. '
                         'Speak a bit longer and a little louder, then try again.')
    return text
