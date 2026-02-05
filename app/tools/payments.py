"""
payments.py
Tool para crear links de pago via Mercado Pago Checkout Pro.
"""

import logging
from typing import Optional
from decimal import Decimal

import httpx

from app.config import get_settings
from app.tools.oauth_mp import ensure_valid_mp_token
from app.infra.sheets import (
    get_order_by_id,
    update_order_preference,
    get_vet_by_id,
)
from app.models.schemas import Order

logger = logging.getLogger(__name__)
settings = get_settings()


def create_payment_link(vet_id: str, order_id: str) -> dict:
    """
    Crea un link de pago de Mercado Pago para un pedido.

    Usa el access_token de la veterinaria (OAuth delegado) para que
    el cobro vaya directo a su cuenta de Mercado Pago.

    Args:
        vet_id: ID de la veterinaria
        order_id: ID del pedido

    Returns:
        dict con:
        - status: 'success' | 'mp_not_connected' | 'order_not_found' | 'error'
        - message: mensaje descriptivo
        - payment_url: URL del checkout (si success)
        - preference_id: ID de la preferencia MP (si success)
    """
    try:
        # 1. Obtener token válido de MP
        token_result = ensure_valid_mp_token(vet_id)

        if token_result["status"] != "success":
            if token_result["status"] == "not_connected":
                return {
                    "status": "mp_not_connected",
                    "message": "Para crear links de pago, primero tenés que conectar tu cuenta de Mercado Pago.",
                }
            return {
                "status": "error",
                "message": token_result.get("message", "Error con la conexión de Mercado Pago."),
            }

        access_token = token_result["access_token"]

        # 2. Obtener datos del pedido
        order = get_order_by_id(order_id)

        if order is None:
            return {
                "status": "order_not_found",
                "message": f"No encontré el pedido {order_id}.",
            }

        # Verificar que el pedido pertenezca a esta veterinaria
        if order.vet_id != vet_id:
            return {
                "status": "error",
                "message": "Este pedido no pertenece a tu veterinaria.",
            }

        # 3. Obtener datos de la veterinaria
        vet = get_vet_by_id(vet_id)
        vet_name = vet.name if vet else vet_id

        # 4. Crear preferencia de pago en MP
        external_reference = f"DTV|{vet_id}|{order_id}"

        preference = _create_mp_preference(
            access_token=access_token,
            order=order,
            vet_name=vet_name,
            vet_id=vet_id,
            external_reference=external_reference,
        )

        if preference is None:
            return {
                "status": "error",
                "message": "No se pudo crear el link de pago. Intentá de nuevo.",
            }

        # 5. Actualizar pedido con datos de MP
        update_order_preference(
            order_id=order_id,
            preference_id=preference["id"],
            external_reference=external_reference,
        )

        # 6. Retornar URL de pago
        # En producción usamos init_point, en sandbox sandbox_init_point
        payment_url = preference.get("init_point") or preference.get("sandbox_init_point")

        logger.info(f"Created payment link for order {order_id}: {preference['id']}")

        return {
            "status": "success",
            "message": "Link de pago creado exitosamente.",
            "payment_url": payment_url,
            "preference_id": preference["id"],
            "external_reference": external_reference,
        }

    except Exception as e:
        logger.error(f"Error creating payment link: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al crear el link de pago.",
        }


def get_payment_link_for_order(order_id: str) -> dict:
    """
    Obtiene el link de pago existente para un pedido.

    Si el pedido ya tiene una preferencia creada, devuelve el link.

    Args:
        order_id: ID del pedido

    Returns:
        dict con:
        - status: 'success' | 'no_link' | 'order_not_found' | 'error'
        - message: mensaje descriptivo
        - payment_url: URL del checkout (si success)
    """
    try:
        order = get_order_by_id(order_id)

        if order is None:
            return {
                "status": "order_not_found",
                "message": f"No encontré el pedido {order_id}.",
            }

        if not order.mp_preference_id:
            return {
                "status": "no_link",
                "message": "Este pedido aún no tiene un link de pago. Primero hay que crearlo.",
            }

        # Obtener preferencia actualizada de MP
        token_result = ensure_valid_mp_token(order.vet_id)

        if token_result["status"] != "success":
            # Si no podemos verificar, devolvemos lo que tenemos
            return {
                "status": "success",
                "message": "Link de pago del pedido.",
                "payment_url": None,  # No podemos obtenerlo sin token
                "preference_id": order.mp_preference_id,
                "note": "No se pudo verificar el link con MP.",
            }

        # Obtener preferencia de MP
        preference = _get_mp_preference(
            access_token=token_result["access_token"],
            preference_id=order.mp_preference_id,
        )

        if preference:
            payment_url = preference.get("init_point") or preference.get("sandbox_init_point")
            return {
                "status": "success",
                "message": "Link de pago del pedido.",
                "payment_url": payment_url,
                "preference_id": order.mp_preference_id,
            }

        return {
            "status": "error",
            "message": "No se pudo obtener el link de pago.",
        }

    except Exception as e:
        logger.error(f"Error getting payment link: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al obtener el link de pago.",
        }


# =============================================================================
# Funciones internas
# =============================================================================

def _create_mp_preference(
    access_token: str,
    order: Order,
    vet_name: str,
    vet_id: str,
    external_reference: str,
) -> Optional[dict]:
    """
    Crea una preferencia de pago en Mercado Pago (Checkout Pro).

    POST https://api.mercadopago.com/checkout/preferences
    """
    try:
        # Construir items
        items = []
        for item in order.items:
            items.append({
                "id": item.product_sku,
                "title": item.product_name,
                "description": f"Pedido {order.order_id} - {vet_name}",
                "quantity": item.quantity,
                "currency_id": order.currency,
                "unit_price": float(item.unit_price),
            })

        # Agregar costo de envío como item si existe
        if order.shipping_cost and float(order.shipping_cost) > 0:
            items.append({
                "id": "SHIPPING",
                "title": f"Envío a {order.delivery.zone or 'domicilio'}",
                "description": f"Costo de envío - {order.order_id}",
                "quantity": 1,
                "currency_id": order.currency,
                "unit_price": float(order.shipping_cost),
            })

        # Datos del pagador
        payer = {
            "name": order.customer.name,
            "surname": order.customer.lastname,
            "email": order.customer.email,
            "phone": {
                "number": order.customer.whatsapp_e164.replace("+", ""),
            },
        }

        # Construir preferencia
        preference_data = {
            "items": items,
            "payer": payer,
            "external_reference": external_reference,
            "statement_descriptor": vet_name[:22],  # Max 22 chars
            "notification_url": f"{settings.webhook_base_url}/mp/webhook/v2?vet_id={vet_id}",
            "back_urls": {
                "success": f"{settings.webhook_base_url}/payment/success?order_id={order.order_id}",
                "failure": f"{settings.webhook_base_url}/payment/failure?order_id={order.order_id}",
                "pending": f"{settings.webhook_base_url}/payment/pending?order_id={order.order_id}",
            },
            "auto_return": "approved",
            "expires": True,
            "expiration_date_from": None,
            "expiration_date_to": None,
        }

        # Llamar a la API
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.mp_api_base_url}/checkout/preferences",
                json=preference_data,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code in (200, 201):
                return response.json()

            logger.error(f"MP preference creation failed: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"Error creating MP preference: {e}")
        return None


def _get_mp_preference(access_token: str, preference_id: str) -> Optional[dict]:
    """
    Obtiene una preferencia de MP por ID.

    GET https://api.mercadopago.com/checkout/preferences/{id}
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{settings.mp_api_base_url}/checkout/preferences/{preference_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
            )

            if response.status_code == 200:
                return response.json()

            logger.error(f"MP preference get failed: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error getting MP preference: {e}")
        return None


def _get_mp_payment(access_token: str, payment_id: str) -> Optional[dict]:
    """
    Obtiene información de un pago de MP.

    GET https://api.mercadopago.com/v1/payments/{id}
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{settings.mp_api_base_url}/v1/payments/{payment_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
            )

            if response.status_code == 200:
                return response.json()

            logger.error(f"MP payment get failed: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error getting MP payment: {e}")
        return None
