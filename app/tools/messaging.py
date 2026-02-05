"""
messaging.py
Tool para enviar mensajes de WhatsApp via Twilio.
"""

import logging
from typing import Optional

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Cliente Twilio (singleton lazy)
_twilio_client: Optional[TwilioClient] = None


def _get_twilio_client() -> Optional[TwilioClient]:
    """Obtiene o crea el cliente de Twilio."""
    global _twilio_client

    if _twilio_client is not None:
        return _twilio_client

    if not settings.has_twilio():
        logger.warning("Twilio credentials not configured")
        return None

    _twilio_client = TwilioClient(
        settings.twilio_account_sid,
        settings.twilio_auth_token,
    )
    return _twilio_client


def send_whatsapp_message(to_e164: str, text: str) -> dict:
    """
    Envía un mensaje de WhatsApp a un número de teléfono.

    Usa la API de Twilio para enviar mensajes vía WhatsApp.
    El número de origen es el configurado en TWILIO_WHATSAPP_NUMBER.

    Args:
        to_e164: Número destino en formato E.164 (ej: +5491155551234)
        text: Texto del mensaje a enviar

    Returns:
        dict con:
        - status: 'sent' | 'not_configured' | 'invalid_number' | 'error'
        - message: mensaje descriptivo
        - message_sid: ID del mensaje en Twilio (si sent)
    """
    try:
        client = _get_twilio_client()

        if client is None:
            logger.warning("Twilio not configured, message not sent")
            return {
                "status": "not_configured",
                "message": "El servicio de WhatsApp no está configurado.",
            }

        # Validar formato del número
        if not to_e164.startswith("+"):
            to_e164 = f"+{to_e164}"

        # Formatear números para WhatsApp
        from_whatsapp = f"whatsapp:{settings.twilio_whatsapp_number}"
        to_whatsapp = f"whatsapp:{to_e164}"

        # Enviar mensaje
        message = client.messages.create(
            body=text,
            from_=from_whatsapp,
            to=to_whatsapp,
        )

        logger.info(f"WhatsApp message sent to {to_e164}: {message.sid}")

        return {
            "status": "sent",
            "message": "Mensaje enviado exitosamente.",
            "message_sid": message.sid,
        }

    except TwilioRestException as e:
        logger.error(f"Twilio error sending message: {e.code} - {e.msg}")

        # Manejar errores comunes
        if e.code == 21211:  # Invalid 'To' Phone Number
            return {
                "status": "invalid_number",
                "message": f"El número {to_e164} no es válido para WhatsApp.",
            }
        elif e.code == 21608:  # Not a WhatsApp number
            return {
                "status": "invalid_number",
                "message": f"El número {to_e164} no tiene WhatsApp activo.",
            }

        return {
            "status": "error",
            "message": "No se pudo enviar el mensaje de WhatsApp.",
            "error_code": e.code,
        }

    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al enviar el mensaje.",
        }


def send_payment_link_to_customer(
    customer_phone: str,
    customer_name: str,
    vet_name: str,
    order_id: str,
    total_amount: str,
    payment_url: str,
) -> dict:
    """
    Envía el link de pago al cliente final.

    Si hay un template de Twilio configurado, lo usa (funciona fuera de 24h).
    Si no, envía mensaje freeform (solo funciona en ventana de 24h).

    Args:
        customer_phone: WhatsApp del cliente en E.164
        customer_name: Nombre del cliente
        vet_name: Nombre de la veterinaria
        order_id: ID del pedido
        total_amount: Monto total formateado (ej: "$15.500,00")
        payment_url: URL de pago de Mercado Pago

    Returns:
        dict con status del envío
    """
    # Si hay template configurado, usarlo (permite envío fuera de 24h window)
    if settings.twilio_payment_template_sid:
        result = _send_payment_template(
            customer_phone=customer_phone,
            customer_name=customer_name,
            vet_name=vet_name,
            order_id=order_id,
            total_amount=total_amount,
            payment_url=payment_url,
        )
    else:
        # Fallback a mensaje freeform (solo funciona en ventana de 24h)
        message_text = f"""Hola {customer_name}!

Tu pedido de *{vet_name}* está listo para pagar.

*Pedido:* {order_id}
*Total:* {total_amount}

Pagá de forma segura con Mercado Pago:
{payment_url}

Gracias por tu compra!
"""
        result = send_whatsapp_message(customer_phone, message_text)

    if result["status"] == "sent":
        result["message"] = f"Link de pago enviado a {customer_name} por WhatsApp."

    return result


def _send_payment_template(
    customer_phone: str,
    customer_name: str,
    vet_name: str,
    order_id: str,
    total_amount: str,
    payment_url: str,
) -> dict:
    """
    Envía el template de pago aprobado por WhatsApp.

    Usa content_sid y content_variables para enviar mensaje con template.
    """
    import json

    try:
        client = _get_twilio_client()

        if client is None:
            return {
                "status": "not_configured",
                "message": "El servicio de WhatsApp no está configurado.",
            }

        # Validar formato del número
        if not customer_phone.startswith("+"):
            customer_phone = f"+{customer_phone}"

        # Formatear números para WhatsApp
        from_whatsapp = f"whatsapp:{settings.twilio_whatsapp_number}"
        to_whatsapp = f"whatsapp:{customer_phone}"

        # Variables del template (orden: {{1}}, {{2}}, {{3}}, {{4}}, {{5}})
        content_variables = json.dumps({
            "1": customer_name,
            "2": vet_name,
            "3": order_id,
            "4": total_amount,
            "5": payment_url,
        })

        # Enviar con template
        message = client.messages.create(
            from_=from_whatsapp,
            to=to_whatsapp,
            content_sid=settings.twilio_payment_template_sid,
            content_variables=content_variables,
        )

        logger.info(f"WhatsApp template message sent to {customer_phone}: {message.sid}")

        return {
            "status": "sent",
            "message": "Mensaje enviado exitosamente.",
            "message_sid": message.sid,
        }

    except TwilioRestException as e:
        logger.error(f"Twilio error sending template: {e.code} - {e.msg}")

        if e.code == 21211:
            return {
                "status": "invalid_number",
                "message": f"El número {customer_phone} no es válido para WhatsApp.",
            }
        elif e.code == 63016:  # Template not approved
            logger.warning("Template not approved, falling back to freeform")
            # Fallback a freeform si el template no está aprobado
            message_text = f"""Hola {customer_name}!

Tu pedido de *{vet_name}* está listo para pagar.

*Pedido:* {order_id}
*Total:* {total_amount}

Pagá de forma segura con Mercado Pago:
{payment_url}

Gracias por tu compra!
"""
            return send_whatsapp_message(customer_phone, message_text)

        return {
            "status": "error",
            "message": "No se pudo enviar el mensaje de WhatsApp.",
            "error_code": e.code,
        }

    except Exception as e:
        logger.error(f"Error sending WhatsApp template: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al enviar el mensaje.",
        }


def send_payment_confirmation_to_vet(
    vet_phone: str,
    vet_name: str,
    customer_name: str,
    order_id: str,
    total_amount: str,
) -> dict:
    """
    Notifica al veterinario que el cliente pagó.

    Args:
        vet_phone: WhatsApp de la veterinaria en E.164
        vet_name: Nombre de la veterinaria
        customer_name: Nombre del cliente
        order_id: ID del pedido
        total_amount: Monto total formateado

    Returns:
        dict con status del envío
    """
    message_text = f"""Pago recibido!

*Pedido:* {order_id}
*Cliente:* {customer_name}
*Total:* {total_amount}

El pago fue acreditado en tu cuenta de Mercado Pago.
"""

    result = send_whatsapp_message(vet_phone, message_text)

    if result["status"] == "sent":
        result["message"] = "Notificación de pago enviada."

    return result


def send_order_status_to_customer(
    customer_phone: str,
    customer_name: str,
    order_id: str,
    status_message: str,
) -> dict:
    """
    Envía actualización de estado del pedido al cliente.

    Args:
        customer_phone: WhatsApp del cliente en E.164
        customer_name: Nombre del cliente
        order_id: ID del pedido
        status_message: Mensaje de estado a enviar

    Returns:
        dict con status del envío
    """
    message_text = f"""Hola {customer_name}!

Actualización de tu pedido *{order_id}*:

{status_message}
"""

    return send_whatsapp_message(customer_phone, message_text)
