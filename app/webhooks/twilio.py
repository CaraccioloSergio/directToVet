"""
twilio.py
Webhook para recibir mensajes entrantes de WhatsApp via Twilio.
Soporta mensajes de texto y audio (transcripción automática).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import get_settings
from app.agent.router import process_incoming_message
from app.infra.audio import process_audio_message

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/twilio", tags=["Twilio Webhook"])


class TwilioInboundMessage(BaseModel):
    """Modelo para mensaje entrante de Twilio."""

    MessageSid: str
    From: str  # formato: whatsapp:+5491155551234
    To: str
    Body: str
    NumMedia: str = "0"
    MediaUrl0: Optional[str] = None
    MediaContentType0: Optional[str] = None
    ProfileName: Optional[str] = None

    @property
    def from_phone(self) -> str:
        """Extrae el número de teléfono sin el prefijo whatsapp:"""
        return self.From.replace("whatsapp:", "")

    @property
    def to_phone(self) -> str:
        """Extrae el número de teléfono destino."""
        return self.To.replace("whatsapp:", "")

    @property
    def has_audio(self) -> bool:
        """Verifica si el mensaje contiene audio."""
        return (
            int(self.NumMedia) > 0
            and self.MediaContentType0
            and self.MediaContentType0.startswith("audio/")
        )


@router.post("/inbound")
async def twilio_inbound_webhook(request: Request) -> Response:
    """
    Endpoint para recibir mensajes entrantes de WhatsApp.

    Twilio envía un POST con los datos del mensaje cuando un usuario
    envía un mensaje al número de WhatsApp configurado.

    Soporta:
    - Mensajes de texto: se procesan directamente
    - Mensajes de audio: se transcriben con Gemini y luego se procesan

    El flujo es:
    1. Recibir mensaje de Twilio
    2. Si es audio, transcribir con Gemini
    3. Enviar texto al agente para procesar
    4. Responder con TwiML vacío (la respuesta va por mensaje separado)
    """
    try:
        # Parsear form data de Twilio
        form_data = await request.form()
        data = dict(form_data)

        logger.info(f"Received Twilio webhook: {data.get('MessageSid', 'unknown')}")

        # Crear objeto de mensaje
        message = TwilioInboundMessage(
            MessageSid=data.get("MessageSid", ""),
            From=data.get("From", ""),
            To=data.get("To", ""),
            Body=data.get("Body", ""),
            NumMedia=data.get("NumMedia", "0"),
            MediaUrl0=data.get("MediaUrl0"),
            MediaContentType0=data.get("MediaContentType0"),
            ProfileName=data.get("ProfileName"),
        )

        # Determinar el texto efectivo (de audio o texto directo)
        effective_text = message.Body

        # Si es un mensaje de audio, transcribirlo
        if message.has_audio:
            logger.info(
                f"Audio message from {message.from_phone}: {message.MediaContentType0}"
            )
            try:
                transcribed = await process_audio_message(
                    media_url=message.MediaUrl0,
                    content_type=message.MediaContentType0,
                )
                if transcribed:
                    effective_text = transcribed
                    logger.info(f"Transcribed audio: '{transcribed[:50]}...'")
                else:
                    logger.warning("Audio transcription failed, using empty text")
                    effective_text = ""
            except Exception as e:
                logger.error(f"Error processing audio: {e}")
                effective_text = ""
        else:
            logger.info(
                f"Text message from {message.from_phone}: {message.Body[:50]}..."
            )

        # Validar que hay texto para procesar
        if not effective_text:
            logger.warning("No text to process (empty message or failed transcription)")
            # Aún así devolvemos 200 para que Twilio no reintente
            return Response(
                content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                media_type="application/xml",
            )

        # Procesar mensaje con el agente
        # Esto es asíncrono - la respuesta va en un mensaje separado
        await process_incoming_message(
            phone_e164=message.from_phone,
            message_text=effective_text,
            message_sid=message.MessageSid,
            profile_name=message.ProfileName,
        )

        # Responder con TwiML vacío
        # La respuesta al usuario se envía por mensaje separado
        twiml_response = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        return Response(
            content=twiml_response,
            media_type="application/xml",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing Twilio webhook: {e}")
        # Aún así devolvemos 200 para que Twilio no reintente
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml",
        )


@router.get("/health")
async def twilio_health():
    """Health check para el webhook de Twilio."""
    return {
        "status": "ok",
        "service": "twilio_webhook",
        "configured": settings.has_twilio(),
    }
