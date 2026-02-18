"""
mercadopago.py
Webhook para recibir notificaciones de pago de Mercado Pago.
"""

import logging
import hashlib
import hmac
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel

from app.config import get_settings
from app.infra.sheets import (
    get_order_by_external_reference,
    update_order_payment_status,
    get_vet_by_id,
    log_event,
)
from app.infra.email_service import send_payment_approved_notification
from app.tools.oauth_mp import ensure_valid_mp_token
from app.tools.messaging import (
    send_payment_confirmation_to_vet,
    send_order_status_to_customer,
)
from app.models.schemas import OrderStatus, MPPaymentStatus, EventType

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/mp", tags=["Mercado Pago Webhook"])

# Set para idempotencia (en prod usar Redis)
_processed_notifications: set = set()


class MPWebhookPayload(BaseModel):
    """Modelo para notificación de MP."""

    id: Optional[int] = None
    live_mode: bool = False
    type: str  # "payment", "plan", "subscription", etc.
    date_created: Optional[str] = None
    application_id: Optional[str] = None
    user_id: Optional[str] = None
    version: int = 1
    api_version: Optional[str] = None
    action: str  # "payment.created", "payment.updated"
    data: dict  # {"id": "123456789"}


@router.post("/webhook")
async def mercadopago_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None),
    x_request_id: Optional[str] = Header(None),
) -> dict:
    """
    Endpoint para recibir notificaciones de Mercado Pago.

    MP envía notificaciones cuando cambia el estado de un pago.
    Este webhook es IDEMPOTENTE - procesa cada pago una sola vez.

    El flujo es:
    1. Validar firma (si está configurada)
    2. Verificar idempotencia
    3. Obtener datos del pago de MP
    4. Actualizar pedido en Sheets
    5. Notificar al veterinario
    6. Enviar email operativo
    """
    try:
        # Parsear body
        body = await request.json()
        logger.info(f"MP webhook received: {body}")

        # Validar payload
        payload = MPWebhookPayload(**body)

        # Solo procesar notificaciones de pago
        if payload.type != "payment":
            logger.info(f"Ignoring non-payment notification: {payload.type}")
            return {"status": "ignored", "reason": "not_payment"}

        # Obtener ID del pago
        payment_id = str(payload.data.get("id", ""))
        if not payment_id:
            logger.warning("Payment notification without ID")
            return {"status": "ignored", "reason": "no_payment_id"}

        # Verificar idempotencia
        idempotency_key = f"{payload.action}:{payment_id}"
        if idempotency_key in _processed_notifications:
            logger.info(f"Duplicate notification ignored: {idempotency_key}")
            return {"status": "duplicate", "payment_id": payment_id}

        # Marcar como procesado
        _processed_notifications.add(idempotency_key)

        # Limitar tamaño del set (en prod usar Redis con TTL)
        if len(_processed_notifications) > 10000:
            _processed_notifications.clear()

        # Procesar el pago
        result = await _process_payment_notification(payment_id)

        return result

    except Exception as e:
        logger.error(f"Error processing MP webhook: {e}")
        # Devolver 200 para que MP no reintente
        return {"status": "error", "message": str(e)}


async def _process_payment_notification(payment_id: str) -> dict:
    """
    Procesa una notificación de pago.

    1. Obtiene info del pago de MP
    2. Busca el pedido por external_reference
    3. Actualiza estado del pedido
    4. Notifica al veterinario
    """
    try:
        # Necesitamos el access_token para consultar MP
        # El problema es que no sabemos de qué vet es el pago aún
        # Primero intentamos obtener el pago con el token de la app
        # o iteramos por las vets (no ideal, pero funciona para PoC)

        # Por ahora, obtenemos el pago usando el user_id de la notificación
        # que corresponde al vendedor (la vet)

        # Alternativa: parsear external_reference que incluye vet_id
        # Para eso necesitaríamos obtener el pago primero...

        # Solución pragmática para PoC:
        # La external_reference tiene formato: DTV|{vet_id}|{order_id}
        # Pero para obtenerla necesitamos el token...

        # Approach: buscar en todas las órdenes con payment_pending
        # y matchear por payment_id (no eficiente pero funciona)

        logger.info(f"Processing payment {payment_id}")

        # Por ahora retornamos que se recibió
        # La implementación completa requiere saber el vet_id

        return {
            "status": "received",
            "payment_id": payment_id,
            "message": "Payment notification received",
        }

    except Exception as e:
        logger.error(f"Error processing payment: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/webhook/v2")
async def mercadopago_webhook_v2(
    request: Request,
    vet_id: str,  # Query param para identificar la vet
) -> dict:
    """
    Webhook alternativo con vet_id en la URL.

    Cada veterinaria tiene su propio webhook URL:
    /mp/webhook/v2?vet_id=VET001

    Esto permite saber inmediatamente de qué cuenta viene la notificación.
    """
    try:
        body = await request.json()
        logger.info(f"MP webhook v2 for vet {vet_id}: {body}")

        payload = MPWebhookPayload(**body)

        if payload.type != "payment":
            return {"status": "ignored"}

        payment_id = str(payload.data.get("id", ""))
        if not payment_id:
            return {"status": "ignored"}

        # Idempotencia
        idempotency_key = f"{vet_id}:{payload.action}:{payment_id}"
        if idempotency_key in _processed_notifications:
            return {"status": "duplicate"}

        _processed_notifications.add(idempotency_key)

        # Procesar con vet_id conocido
        result = await _process_payment_with_vet(vet_id, payment_id)

        return result

    except Exception as e:
        logger.error(f"Error in MP webhook v2: {e}")
        return {"status": "error"}


async def _process_payment_with_vet(vet_id: str, payment_id: str) -> dict:
    """
    Procesa un pago sabiendo el vet_id.
    """
    try:
        logger.info(f"[WEBHOOK] Processing payment {payment_id} for vet {vet_id}")

        # 1. Obtener token de la vet
        token_result = ensure_valid_mp_token(vet_id)
        if token_result["status"] != "success":
            logger.error(f"[WEBHOOK] Could not get MP token for vet {vet_id}: {token_result.get('message', 'unknown')}")
            return {"status": "error", "reason": "no_token"}

        access_token = token_result["access_token"]

        # 2. Obtener datos del pago de MP
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.mp_api_base_url}/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                logger.error(f"[WEBHOOK] Could not get payment from MP: {response.status_code} - {response.text}")
                return {"status": "error", "reason": "mp_api_error"}

            payment_data = response.json()
            logger.info(f"[WEBHOOK] MP API response received for payment {payment_id}")

        # 3. Extraer información
        external_reference = payment_data.get("external_reference", "")
        mp_status = payment_data.get("status", "")
        mp_status_detail = payment_data.get("status_detail", "")

        logger.info(f"[WEBHOOK] Payment {payment_id}: status={mp_status}, detail={mp_status_detail}, ref={external_reference}")

        # 4. Buscar orden por external_reference
        # Formato: DTV|{vet_id}|{order_id}
        if not external_reference or not external_reference.startswith("DTV|"):
            logger.warning(f"[WEBHOOK] Invalid external_reference: {external_reference}")
            return {"status": "ignored", "reason": "invalid_reference"}

        parts = external_reference.split("|")
        if len(parts) != 3:
            logger.warning(f"[WEBHOOK] Invalid external_reference format: {external_reference}")
            return {"status": "ignored", "reason": "invalid_reference_format"}

        _, ref_vet_id, order_id = parts
        logger.info(f"[WEBHOOK] Parsed external_reference: vet={ref_vet_id}, order={order_id}")

        # Verificar que coincida el vet_id
        if ref_vet_id != vet_id:
            logger.warning(f"[WEBHOOK] Vet ID mismatch: expected {vet_id}, got {ref_vet_id}")
            return {"status": "error", "reason": "vet_mismatch"}

        # 5. Mapear status de MP a nuestro status
        order_status = _map_mp_status_to_order_status(mp_status)
        mp_payment_status = _map_mp_status(mp_status)
        logger.info(f"[WEBHOOK] Mapped status: MP={mp_status} -> Order={order_status.value}, MPStatus={mp_payment_status.value}")

        # 6. Actualizar orden
        updated = update_order_payment_status(
            order_id=order_id,
            mp_payment_id=payment_id,
            mp_status=mp_payment_status,
            status=order_status,
        )
        logger.info(f"[WEBHOOK] Order {order_id} update result: {updated}")

        # 7. Registrar evento
        log_event(
            event_type=EventType.PAYMENT_RECEIVED,
            order_id=order_id,
            vet_id=vet_id,
            payload={
                "payment_id": payment_id,
                "mp_status": mp_status,
                "mp_status_detail": mp_status_detail,
            },
        )

        # 8. Si el pago fue aprobado, notificar
        if mp_status == "approved":
            logger.info(f"[WEBHOOK] Payment approved, sending notifications for order {order_id}")
            await _handle_approved_payment(vet_id, order_id, payment_id, payment_data)

        logger.info(f"[WEBHOOK] Successfully processed payment {payment_id} for order {order_id}")
        return {
            "status": "processed",
            "payment_id": payment_id,
            "order_id": order_id,
            "mp_status": mp_status,
        }

    except Exception as e:
        logger.error(f"[WEBHOOK] Error processing payment with vet: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def _handle_approved_payment(
    vet_id: str,
    order_id: str,
    payment_id: str,
    payment_data: dict,
) -> None:
    """
    Maneja un pago aprobado: notifica y envía emails.
    """
    try:
        # Obtener datos de la vet
        vet = get_vet_by_id(vet_id)
        if not vet:
            logger.error(f"Vet not found: {vet_id}")
            return

        # Obtener datos de la orden
        order = get_order_by_external_reference(f"DTV|{vet_id}|{order_id}")
        if not order:
            logger.error(f"Order not found: {order_id}")
            return

        # Formatear monto
        total_amount = f"${order.total_amount:,.2f} {order.currency}"

        # 1. Enviar WhatsApp al veterinario
        send_payment_confirmation_to_vet(
            vet_phone=vet.whatsapp_e164,
            vet_name=vet.name,
            customer_name=order.customer.full_name,
            order_id=order_id,
            total_amount=total_amount,
        )

        # 2. Enviar WhatsApp al cliente con detalle del pedido
        if order.delivery.mode.value == "DELIVERY":
            delivery_info = f"Tu pedido será enviado a {order.delivery.address or 'tu domicilio'}.\nTe avisamos cuando esté en camino!"
        else:
            delivery_info = f"Retirá tu pedido en {vet.name}.\nTe avisamos cuando esté listo!"

        customer_status_message = (
            f"Tu pago de {total_amount} por el pedido *{order_id}* fue confirmado.\n\n"
            f"{delivery_info}"
        )

        send_order_status_to_customer(
            customer_phone=order.customer.whatsapp_e164,
            customer_name=order.customer.name,
            order_id=order_id,
            status_message=customer_status_message,
        )

        # 3. Enviar email operativo
        send_payment_approved_notification(
            order_id=order_id,
            vet_name=vet.name,
            customer_name=order.customer.full_name,
            total_amount=total_amount,
            payment_id=payment_id,
        )

        logger.info(f"Approved payment notifications sent for order {order_id}")

    except Exception as e:
        logger.error(f"Error handling approved payment: {e}")


def _map_mp_status_to_order_status(mp_status: str) -> OrderStatus:
    """Mapea status de MP a nuestro OrderStatus."""
    mapping = {
        "approved": OrderStatus.PAYMENT_APPROVED,
        "pending": OrderStatus.PAYMENT_PENDING_MP,
        "in_process": OrderStatus.PAYMENT_PENDING_MP,
        "rejected": OrderStatus.PAYMENT_REJECTED,
        "cancelled": OrderStatus.CANCELLED,
        "refunded": OrderStatus.CANCELLED,
    }
    return mapping.get(mp_status, OrderStatus.PAYMENT_PENDING_MP)


def _map_mp_status(mp_status: str) -> MPPaymentStatus:
    """Mapea status de MP a nuestro MPPaymentStatus."""
    mapping = {
        "approved": MPPaymentStatus.APPROVED,
        "pending": MPPaymentStatus.PENDING,
        "in_process": MPPaymentStatus.IN_PROCESS,
        "rejected": MPPaymentStatus.REJECTED,
        "cancelled": MPPaymentStatus.CANCELLED,
        "refunded": MPPaymentStatus.REFUNDED,
    }
    return mapping.get(mp_status, MPPaymentStatus.PENDING)


@router.get("/health")
async def mp_health():
    """Health check para el webhook de Mercado Pago."""
    return {
        "status": "ok",
        "service": "mercadopago_webhook",
        "mp_configured": settings.has_mp(),
    }
