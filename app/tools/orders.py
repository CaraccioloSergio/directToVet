"""
orders.py
Tool para crear y gestionar pedidos.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.infra.sheets import (
    create_order_record,
    get_order_by_id,
    update_order_preference,
    update_order_status as sheets_update_order_status,
    set_order_payment_method as sheets_set_payment_method,
    log_event,
    create_customer,
    get_shipping_cost,
)
from app.infra.email_service import send_order_created_notification
from app.models.schemas import (
    Order,
    OrderStatus,
    PaymentMethod,
    CustomerData,
    DeliveryData,
    DeliveryMode,
    CartItem,
    EventType,
)
from app.tools.cart import get_cart_for_order, clear_cart
from app.tools.messaging import send_order_status_to_customer

logger = logging.getLogger(__name__)


# Mapeo de estados a mensajes para el cliente
STATUS_MESSAGES = {
    OrderStatus.PREPARING: "Estamos preparando tu pedido. ¡Pronto estará listo!",
    OrderStatus.READY_FOR_PICKUP: "¡Tu pedido está listo para retirar! Te esperamos.",
    OrderStatus.OUT_FOR_DELIVERY: "Tu pedido está en camino. ¡Ya casi llega!",
    OrderStatus.DELIVERED: "¡Tu pedido fue entregado! Gracias por tu compra.",
    OrderStatus.COMPLETED: "Tu pedido fue completado. ¡Gracias por elegirnos!",
    OrderStatus.CANCELLED: "Tu pedido fue cancelado. Si tenés dudas, contactanos.",
}


def create_order(
    session_id: str,
    vet_id: str,
    customer_name: str,
    customer_lastname: str,
    customer_email: str,
    customer_whatsapp: str,
    delivery_mode: str = "PICKUP",
    delivery_address: Optional[str] = None,
    delivery_zone: Optional[str] = None,
) -> dict:
    """
    Crea un pedido a partir del carrito actual.

    Valida que el carrito tenga items, crea el registro en Google Sheets,
    y envía notificación al equipo de OPS.

    Args:
        session_id: ID de la sesión (para obtener el carrito)
        vet_id: ID de la veterinaria
        customer_name: Nombre del cliente final
        customer_lastname: Apellido del cliente final
        customer_email: Email del cliente final
        customer_whatsapp: WhatsApp del cliente final (formato E.164)
        delivery_mode: Modo de entrega ("PICKUP" o "DELIVERY")
        delivery_address: Dirección de entrega (requerido si delivery_mode="DELIVERY")
        delivery_zone: Localidad AMBA para calcular costo de envío (requerido si delivery_mode="DELIVERY")

    Returns:
        dict con:
        - status: 'created' | 'empty_cart' | 'validation_error' | 'error'
        - message: mensaje descriptivo
        - order: datos del pedido creado
        - order_id: ID del pedido
    """
    try:
        # Obtener carrito
        cart = get_cart_for_order(session_id)
        if cart is None:
            return {
                "status": "empty_cart",
                "message": "El carrito está vacío. Agregá productos antes de crear el pedido.",
            }

        # Validar datos del cliente
        try:
            customer = CustomerData(
                name=customer_name.strip(),
                lastname=customer_lastname.strip(),
                email=customer_email.strip().lower(),
                whatsapp_e164=customer_whatsapp,
            )
        except Exception as e:
            logger.warning(f"Customer data validation error: {e}")
            return {
                "status": "validation_error",
                "message": f"Error en los datos del cliente: {str(e)}",
            }

        # Validar delivery
        try:
            mode = DeliveryMode(delivery_mode.upper())
            if mode == DeliveryMode.DELIVERY:
                if not delivery_address:
                    return {
                        "status": "validation_error",
                        "message": "Para envío a domicilio, necesito la dirección de entrega.",
                    }
                if not delivery_zone:
                    return {
                        "status": "validation_error",
                        "message": "Para envío a domicilio, necesito la localidad (zona AMBA) para calcular el costo de envío.",
                    }
            delivery = DeliveryData(
                mode=mode,
                address=delivery_address,
                zone=delivery_zone if mode == DeliveryMode.DELIVERY else None,
            )
        except ValueError:
            return {
                "status": "validation_error",
                "message": f"Modo de entrega inválido: {delivery_mode}. Usá PICKUP o DELIVERY.",
            }

        # Calcular costo de envío
        shipping_cost = Decimal("0")
        if mode == DeliveryMode.DELIVERY and delivery_zone:
            shipping_cost_result = get_shipping_cost(delivery_zone)
            if shipping_cost_result is None:
                return {
                    "status": "validation_error",
                    "message": f"La localidad '{delivery_zone}' no está en nuestra zona de cobertura. Verificá que esté bien escrita o consultá las zonas disponibles.",
                }
            shipping_cost = shipping_cost_result

        # Registrar cliente (si no existe, lo crea)
        create_customer(
            vet_id=vet_id,
            name=customer_name.strip(),
            lastname=customer_lastname.strip(),
            email=customer_email.strip().lower(),
            whatsapp_e164=customer_whatsapp,
            address=delivery_address,
        )

        # Generar ID único
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        # Calcular totales
        subtotal = cart.total_amount
        total_amount = subtotal + shipping_cost

        # Crear orden
        order = Order(
            order_id=order_id,
            vet_id=vet_id,
            customer=customer,
            delivery=delivery,
            items=cart.items,
            subtotal=subtotal,
            shipping_cost=shipping_cost,
            total_amount=total_amount,
            currency=cart.currency,
            status=OrderStatus.CREATED,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        # Guardar en Google Sheets
        if not create_order_record(order):
            return {
                "status": "error",
                "message": "Hubo un problema al guardar el pedido. Intentá de nuevo.",
            }

        # Registrar evento
        log_event(
            event_type=EventType.ORDER_CREATED,
            order_id=order_id,
            vet_id=vet_id,
            payload={
                "customer_email": customer.email,
                "total_amount": str(order.total_amount),
                "items_count": len(order.items),
            },
        )

        # Limpiar carrito
        clear_cart(session_id)

        # Enviar notificación por email
        items_summary = "\n".join([
            f"- {item.quantity}x {item.product_name}: ${item.subtotal:,.2f}"
            for item in order.items
        ])
        send_order_created_notification(
            order_id=order_id,
            vet_name=vet_id,  # TODO: obtener nombre de la vet
            customer_name=customer.full_name,
            total_amount=f"${order.total_amount:,.2f} {order.currency}",
            items_summary=items_summary,
        )

        logger.info(f"Order created: {order_id} for vet {vet_id}")

        return {
            "status": "created",
            "message": f"Pedido {order_id} creado exitosamente.",
            "order_id": order_id,
            "order": {
                "order_id": order_id,
                "vet_id": vet_id,
                "customer": {
                    "name": customer.full_name,
                    "email": customer.email,
                    "whatsapp": customer.whatsapp_e164,
                },
                "delivery": {
                    "mode": delivery.mode.value,
                    "address": delivery.address,
                    "zone": delivery.zone,
                },
                "items": [
                    {
                        "sku": item.product_sku,
                        "name": item.product_name,
                        "quantity": item.quantity,
                        "unit_price": float(item.unit_price),
                        "subtotal": float(item.subtotal),
                    }
                    for item in order.items
                ],
                "subtotal": float(order.subtotal),
                "shipping_cost": float(order.shipping_cost),
                "total_amount": float(order.total_amount),
                "currency": order.currency,
                "status": order.status.value,
            },
        }

    except Exception as e:
        logger.error(f"Error creating order: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al crear el pedido. Intentá de nuevo.",
        }


def get_shipping_cost(zone: str) -> dict:
    """
    Consulta el costo de envío para una localidad AMBA antes de crear el pedido.

    Usá esta función cuando el cliente elige DELIVERY para calcular el costo
    de envío e incluirlo en el resumen de confirmación.

    Args:
        zone: Nombre de la localidad/partido (ej: "CABA", "San Isidro", "Isidro Casanova", "La Plata")

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'error'
        - zone: nombre de la zona consultada
        - shipping_cost: costo de envío como float (si found)
        - message: mensaje descriptivo
    """
    from app.infra.sheets import get_shipping_cost as _get_shipping_cost
    try:
        cost = _get_shipping_cost(zone)
        if cost is None:
            return {
                "status": "not_found",
                "zone": zone,
                "message": f"La localidad '{zone}' no está en la zona de cobertura. Verificá el nombre o consultá las zonas disponibles.",
            }
        return {
            "status": "found",
            "zone": zone,
            "shipping_cost": float(cost),
            "message": f"Envío a {zone}: ${cost:,.2f}",
        }
    except Exception as e:
        logger.error(f"Error getting shipping cost for zone '{zone}': {e}")
        return {
            "status": "error",
            "zone": zone,
            "message": "Hubo un problema al calcular el costo de envío. Intentá de nuevo.",
        }


def get_order_status(order_id: str) -> dict:
    """
    Obtiene el estado de un pedido.

    Args:
        order_id: ID del pedido

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'error'
        - message: mensaje descriptivo
        - order: datos del pedido (si found)
    """
    try:
        order = get_order_by_id(order_id)

        if order is None:
            return {
                "status": "not_found",
                "message": f"No encontré el pedido {order_id}.",
            }

        # Formatear estado para mostrar
        status_display = {
            OrderStatus.CREATED: "Creado",
            OrderStatus.PAYMENT_PENDING: "Esperando pago",
            OrderStatus.PAYMENT_APPROVED: "Pago aprobado",
            OrderStatus.PAYMENT_REJECTED: "Pago rechazado",
            OrderStatus.PREPARING: "En preparación",
            OrderStatus.READY_FOR_PICKUP: "Listo para retirar",
            OrderStatus.OUT_FOR_DELIVERY: "En camino",
            OrderStatus.DELIVERED: "Entregado",
            OrderStatus.CANCELLED: "Cancelado",
            OrderStatus.COMPLETED: "Completado",
        }.get(order.status, order.status.value)

        return {
            "status": "found",
            "message": f"Estado del pedido {order_id}: {status_display}",
            "order": {
                "order_id": order.order_id,
                "status": order.status.value,
                "status_display": status_display,
                "customer_name": order.customer.full_name,
                "total_amount": float(order.total_amount),
                "currency": order.currency,
                "items_count": len(order.items),
                "created_at": order.created_at.isoformat(),
                "mp_payment_id": order.mp_payment_id,
                "mp_status": order.mp_status.value if order.mp_status else None,
            },
        }

    except Exception as e:
        logger.error(f"Error getting order status: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al consultar el estado del pedido.",
        }


def cancel_order(
    order_id: str,
    notify_customer: bool = True,
) -> dict:
    """
    Cancela un pedido.

    Esta es la única acción de cambio de estado que la veterinaria puede
    ejecutar via el agente. Los estados logísticos (PREPARING, READY_FOR_PICKUP,
    OUT_FOR_DELIVERY, DELIVERED, COMPLETED) son gestionados por la distribuidora.

    Args:
        order_id: ID del pedido (ej: "ORD-ABC123")
        notify_customer: Si True, envía WhatsApp al cliente (default: True)

    Returns:
        dict con:
        - status: 'cancelled' | 'not_found' | 'already_cancelled' | 'cannot_cancel' | 'error'
        - message: mensaje descriptivo
        - notified: si se notificó al cliente
    """
    try:
        # Obtener pedido actual
        order = get_order_by_id(order_id)
        if order is None:
            return {
                "status": "not_found",
                "message": f"No encontré el pedido {order_id}.",
            }

        old_status = order.status

        # Verificar si ya está cancelado
        if old_status == OrderStatus.CANCELLED:
            return {
                "status": "already_cancelled",
                "message": f"El pedido {order_id} ya está cancelado.",
            }

        # Verificar si se puede cancelar (no permitir si ya fue entregado o completado)
        non_cancellable = [OrderStatus.DELIVERED, OrderStatus.COMPLETED]
        if old_status in non_cancellable:
            return {
                "status": "cannot_cancel",
                "message": f"No se puede cancelar el pedido {order_id} porque ya fue {old_status.value.lower()}.",
            }

        # Cancelar en sheets
        if not sheets_update_order_status(order_id, OrderStatus.CANCELLED):
            return {
                "status": "error",
                "message": "Hubo un problema al cancelar el pedido. Intentá de nuevo.",
            }

        # Registrar evento
        log_event(
            event_type=EventType.ORDER_CANCELLED,
            order_id=order_id,
            vet_id=order.vet_id,
            payload={
                "old_status": old_status.value,
                "source": "agent",
            },
        )

        # Notificar al cliente
        notified = False
        if notify_customer:
            message = STATUS_MESSAGES[OrderStatus.CANCELLED]
            result = send_order_status_to_customer(
                customer_phone=order.customer.whatsapp_e164,
                customer_name=order.customer.name,
                order_id=order_id,
                status_message=message,
            )
            notified = result.get("status") == "sent"
            if notified:
                logger.info(f"Customer notified about order {order_id} cancellation")

        logger.info(f"Order {order_id} cancelled (was: {old_status.value})")

        return {
            "status": "cancelled",
            "message": f"Pedido {order_id} cancelado." +
                       (" Se notificó al cliente." if notified else ""),
            "order_id": order_id,
            "old_status": old_status.value,
            "notified": notified,
        }

    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al cancelar el pedido.",
        }


def update_order_status(
    order_id: str,
    new_status: str,
    notify_customer: bool = True,
) -> dict:
    """
    DEPRECATED: Esta función está restringida.

    La veterinaria NO puede cambiar estados logísticos via el agente.
    Solo puede cancelar pedidos usando cancel_order().

    Los estados logísticos (PREPARING, READY_FOR_PICKUP, OUT_FOR_DELIVERY,
    DELIVERED, COMPLETED) son gestionados exclusivamente por la distribuidora.

    Args:
        order_id: ID del pedido
        new_status: Estado solicitado

    Returns:
        dict con mensaje de error indicando la restricción
    """
    # Estados que maneja la distribuidora (no el agente)
    distributor_states = [
        "PREPARING", "READY_FOR_PICKUP", "OUT_FOR_DELIVERY",
        "DELIVERED", "COMPLETED"
    ]

    new_status_upper = new_status.upper()

    # Si pide cancelar, redirigir a cancel_order
    if new_status_upper == "CANCELLED":
        return cancel_order(order_id, notify_customer)

    # Si pide un estado de distribuidora, informar que no tiene permiso
    if new_status_upper in distributor_states:
        return {
            "status": "not_allowed",
            "message": (
                f"No podés cambiar el pedido a '{new_status}'. "
                "Los estados de preparación y entrega son gestionados por la distribuidora. "
                "Si necesitás cancelar el pedido, decime y lo cancelo."
            ),
        }

    # Para estados de pago (PAYMENT_*) tampoco permitir cambio manual
    payment_states = ["PAYMENT_PENDING", "PAYMENT_APPROVED", "PAYMENT_REJECTED", "CREATED"]
    if new_status_upper in payment_states:
        return {
            "status": "not_allowed",
            "message": (
                f"No podés cambiar el pedido a '{new_status}'. "
                "Los estados de pago se actualizan automáticamente desde Mercado Pago."
            ),
        }

    # Cualquier otro caso
    return {
        "status": "invalid_status",
        "message": f"Estado inválido o no permitido: {new_status}.",
    }


def set_payment_method(order_id: str, payment_method: str) -> dict:
    """
    Establece el método de pago de un pedido.

    Usar después de crear el pedido para indicar cómo pagará el cliente:
    - MERCADOPAGO: El cliente recibirá un link de pago
    - AT_VET: El cliente pagará en el mostrador de la veterinaria

    Args:
        order_id: ID del pedido (ej: "ORD-ABC123")
        payment_method: Método de pago. Valores válidos:
            - "MERCADOPAGO": Pago online con link de Mercado Pago
            - "AT_VET": Pago en mostrador de la veterinaria

    Returns:
        dict con:
        - status: 'updated' | 'not_found' | 'invalid_method' | 'error'
        - message: mensaje descriptivo
    """
    try:
        # Validar método de pago
        method_upper = payment_method.upper()
        if method_upper not in ["MERCADOPAGO", "AT_VET"]:
            return {
                "status": "invalid_method",
                "message": f"Método de pago inválido: {payment_method}. Usá 'MERCADOPAGO' o 'AT_VET'.",
            }

        # Obtener pedido
        order = get_order_by_id(order_id)
        if order is None:
            return {
                "status": "not_found",
                "message": f"No encontré el pedido {order_id}.",
            }

        # Determinar nuevo estado según método
        if method_upper == "MERCADOPAGO":
            # Para MP, el estado se actualiza cuando se crea el link
            # Aquí solo registramos la intención
            new_status = OrderStatus.CREATED  # Mantener CREATED hasta crear link
            message = (
                f"Pedido {order_id} configurado para pago con Mercado Pago. "
                "Ahora generá el link de pago."
            )
        else:  # AT_VET
            new_status = OrderStatus.PAYMENT_AT_VET
            message = (
                f"Pedido {order_id} configurado para pago en mostrador. "
                "El cliente pagará cuando retire/reciba el pedido."
            )

        # Actualizar en sheets
        if not sheets_set_payment_method(order_id, method_upper, new_status):
            return {
                "status": "error",
                "message": "Hubo un problema al actualizar el método de pago. Intentá de nuevo.",
            }

        # Registrar evento
        log_event(
            event_type=EventType.ORDER_STATUS_CHANGED,
            order_id=order_id,
            vet_id=order.vet_id,
            payload={
                "payment_method": method_upper,
                "new_status": new_status.value,
                "source": "agent",
            },
        )

        logger.info(f"Order {order_id} payment method set to: {method_upper}")

        return {
            "status": "updated",
            "message": message,
            "order_id": order_id,
            "payment_method": method_upper,
            "new_status": new_status.value,
        }

    except Exception as e:
        logger.error(f"Error setting payment method: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al configurar el método de pago.",
        }
