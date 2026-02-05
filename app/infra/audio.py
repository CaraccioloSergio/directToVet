"""
audio.py
Utilidades para procesamiento de audio (descarga y transcripción).
"""

import os
import logging
import tempfile
import subprocess
from typing import Optional
import base64

import httpx
from google.genai import Client  # type: ignore
from google.genai import types  # type: ignore

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def download_twilio_media(media_url: str, content_type: str) -> Optional[str]:
    """
    Descarga un archivo multimedia desde los servidores de Twilio.

    Args:
        media_url: URL del archivo en Twilio
        content_type: MIME type del archivo (audio/ogg, audio/mpeg, etc.)

    Returns:
        Path al archivo descargado, o None si hay error
    """
    # Determinar extensión según MIME type
    ext_map = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/amr": ".amr",
        "audio/3gpp": ".3gp",
    }
    ext = ext_map.get(content_type, ".ogg")

    try:
        # Crear archivo temporal
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp_path = tmp.name
        tmp.close()

        # Descargar con autenticación Twilio
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                media_url,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                follow_redirects=True,
            )
            response.raise_for_status()

            with open(tmp_path, "wb") as f:
                f.write(response.content)

        logger.info(f"Downloaded audio from Twilio: {tmp_path} ({len(response.content)} bytes)")
        return tmp_path

    except Exception as e:
        logger.error(f"Error downloading Twilio media: {e}")
        return None


def convert_to_wav(input_path: str) -> Optional[str]:
    """
    Convierte audio a WAV mono 16kHz (óptimo para STT).

    Requiere FFmpeg instalado en el sistema.

    Args:
        input_path: Path al archivo de audio original

    Returns:
        Path al archivo WAV convertido, o None si hay error
    """
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        output_path = tmp.name
        tmp.close()

        # FFmpeg: convertir a mono 16kHz
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_path,
                "-ac", "1",       # Mono
                "-ar", "16000",   # 16kHz sample rate
                "-acodec", "pcm_s16le",  # PCM 16-bit
                output_path
            ],
            capture_output=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.warning(f"FFmpeg conversion failed: {result.stderr.decode()}")
            # Si falla FFmpeg, devolver el original
            os.remove(output_path)
            return None

        logger.info(f"Converted audio to WAV: {output_path}")
        return output_path

    except FileNotFoundError:
        logger.warning("FFmpeg not found in PATH, using original audio")
        return None
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg conversion timed out")
        return None
    except Exception as e:
        logger.error(f"Error converting audio: {e}")
        return None


def transcribe_audio_gemini(audio_path: str) -> Optional[str]:
    """
    Transcribe audio usando Gemini (google-genai SDK).

    Args:
        audio_path: Path al archivo de audio

    Returns:
        Texto transcrito, o None si hay error
    """
    try:
        # Leer archivo como bytes
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        # Detectar MIME type según extensión
        ext = os.path.splitext(audio_path)[1].lower()
        mime_map = {
            ".ogg": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".amr": "audio/amr",
            ".3gp": "audio/3gpp",
        }
        mime_type = mime_map.get(ext, "audio/ogg")

        # Crear cliente de Gemini
        client = Client(api_key=settings.google_api_key)

        # Crear contenido multimodal para transcripción
        # Usar el modelo flash para transcripción (más rápido y económico)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=mime_type,
                ),
                "Transcribí este audio en español. "
                "Devolvé únicamente el texto transcrito, sin explicaciones adicionales ni comillas.",
            ],
        )

        text = response.text.strip()
        logger.info(f"Transcribed audio: '{text[:50]}...' (total: {len(text)} chars)")
        return text

    except Exception as e:
        logger.error(f"Error transcribing audio with Gemini: {e}")
        return None


async def process_audio_message(media_url: str, content_type: str) -> Optional[str]:
    """
    Procesa un mensaje de audio completo: descarga, convierte y transcribe.

    Args:
        media_url: URL del audio en Twilio
        content_type: MIME type del audio

    Returns:
        Texto transcrito, o None si hay error
    """
    audio_path = None
    wav_path = None

    try:
        # 1. Descargar audio
        audio_path = await download_twilio_media(media_url, content_type)
        if not audio_path:
            return None

        # 2. Intentar convertir a WAV (opcional, mejora calidad)
        wav_path = convert_to_wav(audio_path)
        transcription_path = wav_path if wav_path else audio_path

        # 3. Transcribir con Gemini
        text = transcribe_audio_gemini(transcription_path)
        return text

    finally:
        # Limpiar archivos temporales
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass
        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
