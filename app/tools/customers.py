"""
customers.py
Tool para buscar y gestionar clientes.
"""

import logging
from typing import Optional

from app.infra.sheets import (
    search_customers,
    get_orders_by_customer,
    create_customer as sheets_create_customer,
    update_customer as sheets_update_customer,
)
from app.models.schemas import OrderStatus

logger = logging.getLogger(__name__)


def register_customer(
    vet_id: str,
    name: str,
    lastname: str,
    email: str,
    whatsapp: str,
    address: Optional[str] = None,
    pet_type: Optional[str] = None,
    pet_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Registra un nuevo cliente para la veterinaria.

    Si el cliente ya existe (mismo teléfono o email), retorna el existente
    sin crear duplicado.

    Args:
        vet_id: ID de la veterinaria
        name: Nombre del cliente
        lastname: Apellido del cliente
        email: Email del cliente
        whatsapp: Número de WhatsApp en formato E.164 (ej: +5491112345678)
        address: Dirección del cliente (opcional)
        pet_type: Tipo de mascota (Perro, Gato, etc.) (opcional)
        pet_name: Nombre de la mascota (opcional)
        notes: Notas adicionales (opcional)

    Returns:
        dict con:
        - status: 'created' | 'existing' | 'error'
        - message: mensaje descriptivo
        - customer: datos del cliente
    """
    try:
        customer = sheets_create_customer(
            vet_id=vet_id,
            name=name,
            lastname=lastname,
            email=email,
            whatsapp_e164=whatsapp,
            address=address,
            pet_type=pet_type,
            pet_name=pet_name,
            notes=notes,
        )

        if customer is None:
            return {
                "status": "error",
                "message": "Hubo un problema al registrar el cliente. Verificá los datos e intentá de nuevo.",
            }

        # Determinar si es nuevo o existente
        # (sheets_create_customer retorna existente si ya hay uno con mismo tel/email)
        is_new = customer.customer_id.startswith("CUST-")  # Siempre será así, pero chequeamos

        return {
            "status": "created",
            "message": f"Cliente {customer.full_name} registrado exitosamente.",
            "customer": {
                "customer_id": customer.customer_id,
                "name": customer.full_name,
                "email": customer.email,
                "whatsapp": customer.whatsapp_e164,
                "address": customer.address,
                "pet_type": customer.pet_type,
                "pet_name": customer.pet_name,
            },
        }

    except Exception as e:
        logger.error(f"Error registering customer: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al registrar el cliente.",
        }


def search_customer(
    vet_id: str,
    query: str = "",
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> dict:
    """
    Busca clientes de la veterinaria.

    Puede buscar por nombre (query), teléfono o email.
    Solo devuelve clientes de la veterinaria especificada.

    Args:
        vet_id: ID de la veterinaria
        query: Nombre o apellido a buscar
        phone: Teléfono exacto a buscar
        email: Email exacto a buscar

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'error'
        - message: mensaje descriptivo
        - customers: lista de clientes encontrados
    """
    try:
        customers = search_customers(
            vet_id=vet_id,
            query=query,
            phone=phone,
            email=email,
        )

        if not customers:
            search_criteria = query or phone or email or "todos"
            return {
                "status": "not_found",
                "message": f"No encontré clientes que coincidan con: {search_criteria}",
                "customers": [],
            }

        # Formatear resultados
        customer_list = [
            {
                "customer_id": c.customer_id,
                "name": c.full_name,
                "email": c.email,
                "whatsapp": c.whatsapp_e164,
                "address": c.address,
                "pet_type": c.pet_type,
                "pet_name": c.pet_name,
            }
            for c in customers
        ]

        return {
            "status": "found",
            "message": f"Encontré {len(customers)} cliente(s).",
            "customers": customer_list,
        }

    except Exception as e:
        logger.error(f"Error searching customers: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al buscar clientes.",
        }


def search_order(
    vet_id: str,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    customer_email: Optional[str] = None,
    order_id: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> dict:
    """
    Busca pedidos de un cliente o por ID de orden.

    Puede buscar por nombre del cliente, teléfono, email, o ID de pedido.
    Opcionalmente puede filtrar por estado del pedido.

    Args:
        vet_id: ID de la veterinaria
        customer_name: Nombre del cliente a buscar
        customer_phone: Teléfono del cliente
        customer_email: Email del cliente
        order_id: ID específico de pedido
        status_filter: Filtrar por estado (CREATED, PAYMENT_PENDING, PAYMENT_APPROVED, etc.)

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'error'
        - message: mensaje descriptivo
        - orders: lista de pedidos encontrados
    """
    try:
        # Si se busca por order_id específico
        if order_id:
            from app.infra.sheets import get_order_by_id
            order = get_order_by_id(order_id)

            if order is None:
                return {
                    "status": "not_found",
                    "message": f"No encontré el pedido {order_id}.",
                    "orders": [],
                }

            # Verificar que pertenece al vet
            if order.vet_id != vet_id:
                return {
                    "status": "not_found",
                    "message": f"El pedido {order_id} no pertenece a esta veterinaria.",
                    "orders": [],
                }

            return {
                "status": "found",
                "message": f"Encontré el pedido {order_id}.",
                "orders": [_format_order(order)],
            }

        # Parsear filtro de status
        status = None
        if status_filter:
            try:
                status = OrderStatus(status_filter.upper())
            except ValueError:
                pass

        # Buscar por criterios de cliente
        orders = get_orders_by_customer(
            vet_id=vet_id,
            customer_phone=customer_phone,
            customer_email=customer_email,
            customer_name=customer_name,
            status=status,
        )

        if not orders:
            criteria = customer_name or customer_phone or customer_email or "especificados"
            return {
                "status": "not_found",
                "message": f"No encontré pedidos para el cliente: {criteria}",
                "orders": [],
            }

        # Limitar a los últimos 10
        orders = orders[:10]

        order_list = [_format_order(o) for o in orders]

        return {
            "status": "found",
            "message": f"Encontré {len(orders)} pedido(s).",
            "orders": order_list,
        }

    except Exception as e:
        logger.error(f"Error searching orders: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al buscar pedidos.",
        }


def get_my_orders(customer_phone: str) -> dict:
    """
    Obtiene los pedidos de un cliente por su número de teléfono.

    Esta función es para uso exclusivo del CLIENTE cuando consulta
    sus propios pedidos. Solo muestra información básica del estado.

    Args:
        customer_phone: Número de WhatsApp del cliente (E.164)

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'error'
        - message: mensaje descriptivo
        - orders: lista de pedidos del cliente
    """
    try:
        from app.infra.sheets import normalize_phone

        phone_normalized = normalize_phone(customer_phone)
        if not phone_normalized:
            return {
                "status": "error",
                "message": "Número de teléfono inválido.",
            }

        # Buscar pedidos del cliente en todos los vets
        # (el cliente puede tener pedidos en múltiples veterinarias)
        from app.infra.sheets import get_worksheet, get_settings
        settings = get_settings()

        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        orders = []
        for row in records:
            try:
                row_phone = normalize_phone(row.get("customer_whatsapp_e164", ""))
                if row_phone != phone_normalized:
                    continue

                # Formatear para el cliente (info limitada)
                status_display = {
                    "CREATED": "Creado",
                    "PAYMENT_PENDING": "Esperando tu pago",
                    "PAYMENT_APPROVED": "Pago confirmado",
                    "PAYMENT_REJECTED": "Pago rechazado",
                    "PREPARING": "En preparación",
                    "READY_FOR_PICKUP": "Listo para retirar",
                    "OUT_FOR_DELIVERY": "En camino",
                    "DELIVERED": "Entregado",
                    "CANCELLED": "Cancelado",
                    "COMPLETED": "Completado",
                }.get(row.get("status", ""), row.get("status", ""))

                orders.append({
                    "order_id": row.get("order_id", ""),
                    "status": row.get("status", ""),
                    "status_display": status_display,
                    "total": row.get("total_amount", ""),
                    "delivery_mode": row.get("delivery_mode", ""),
                    "created_at": row.get("created_at", ""),
                })
            except Exception as e:
                logger.warning(f"Error parsing order row for customer: {e}")
                continue

        if not orders:
            return {
                "status": "not_found",
                "message": "No encontré pedidos asociados a tu número.",
                "orders": [],
            }

        # Ordenar por fecha (más recientes primero) y limitar
        orders = sorted(orders, key=lambda x: x.get("created_at", ""), reverse=True)[:5]

        return {
            "status": "found",
            "message": f"Encontré {len(orders)} pedido(s).",
            "orders": orders,
        }

    except Exception as e:
        logger.error(f"Error getting customer orders: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al buscar tus pedidos.",
        }


def _format_order(order) -> dict:
    """Formatea un pedido para devolver al agente."""
    status_display = {
        OrderStatus.CREATED: "Creado (sin link de pago)",
        OrderStatus.PAYMENT_PENDING: "Esperando pago",
        OrderStatus.PAYMENT_APPROVED: "Pagado",
        OrderStatus.PAYMENT_REJECTED: "Pago rechazado",
        OrderStatus.PREPARING: "En preparación",
        OrderStatus.READY_FOR_PICKUP: "Listo para retirar",
        OrderStatus.OUT_FOR_DELIVERY: "En camino",
        OrderStatus.DELIVERED: "Entregado",
        OrderStatus.CANCELLED: "Cancelado",
        OrderStatus.COMPLETED: "Completado",
    }.get(order.status, order.status.value)

    return {
        "order_id": order.order_id,
        "customer_name": order.customer.full_name,
        "customer_phone": order.customer.whatsapp_e164,
        "customer_email": order.customer.email,
        "total_amount": float(order.total_amount),
        "total_formatted": f"${order.total_amount:,.2f} {order.currency}",
        "status": order.status.value,
        "status_display": status_display,
        "delivery_mode": order.delivery.mode.value,
        "delivery_address": order.delivery.address,
        "items_count": len(order.items),
        "items": [
            {
                "name": item.product_name,
                "quantity": item.quantity,
                "subtotal": float(item.subtotal),
            }
            for item in order.items
        ],
        "mp_preference_id": order.mp_preference_id,
        "mp_payment_id": order.mp_payment_id,
        "created_at": order.created_at.isoformat(),
    }


def update_customer_info(
    customer_id: str,
    address: Optional[str] = None,
    email: Optional[str] = None,
    whatsapp: Optional[str] = None,
    pet_type: Optional[str] = None,
    pet_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Actualiza información de un cliente existente.

    Permite actualizar dirección, email, teléfono, mascota o notas.
    Solo actualiza los campos que se proporcionan.

    Args:
        customer_id: ID del cliente a actualizar (ej: CUST-ABC12345)
        address: Nueva dirección (opcional)
        email: Nuevo email (opcional)
        whatsapp: Nuevo teléfono en formato E.164 (opcional)
        pet_type: Tipo de mascota (Perro, Gato, etc.) (opcional)
        pet_name: Nombre de la mascota (opcional)
        notes: Nuevas notas (opcional)

    Returns:
        dict con:
        - status: 'updated' | 'not_found' | 'error'
        - message: mensaje descriptivo
    """
    try:
        if not customer_id:
            return {
                "status": "error",
                "message": "Necesito el ID del cliente para actualizar sus datos.",
            }

        # Verificar que al menos un campo se quiere actualizar
        if not any([address, email, whatsapp, pet_type, pet_name, notes]):
            return {
                "status": "error",
                "message": "No especificaste qué datos actualizar.",
            }

        success = sheets_update_customer(
            customer_id=customer_id,
            address=address,
            email=email,
            whatsapp_e164=whatsapp,
            pet_type=pet_type,
            pet_name=pet_name,
            notes=notes,
        )

        if success:
            updated_fields = []
            if address:
                updated_fields.append("dirección")
            if email:
                updated_fields.append("email")
            if whatsapp:
                updated_fields.append("teléfono")
            if pet_type:
                updated_fields.append("tipo de mascota")
            if pet_name:
                updated_fields.append("nombre de mascota")
            if notes:
                updated_fields.append("notas")

            return {
                "status": "updated",
                "message": f"Actualicé {', '.join(updated_fields)} del cliente {customer_id}.",
            }
        else:
            return {
                "status": "not_found",
                "message": f"No encontré el cliente {customer_id}. Verificá el ID.",
            }

    except Exception as e:
        logger.error(f"Error updating customer: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al actualizar el cliente.",
        }
