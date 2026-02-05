"""
cart.py
Tools para manejar el carrito de compras.
El carrito vive en memoria del agente (AgentMemory).
"""

import logging
from decimal import Decimal
from typing import Optional

from app.infra.sheets import get_product_by_sku
from app.models.schemas import CartItem, CartSummary, Product

logger = logging.getLogger(__name__)


# ===========================================
# CARRITO EN MEMORIA
# Almacenamiento simple por sesión.
# En producción podría usar Redis o similar.
# ===========================================

_carts: dict[str, CartSummary] = {}


def _get_cart(session_id: str) -> CartSummary:
    """Obtiene o crea el carrito de una sesión."""
    if session_id not in _carts:
        _carts[session_id] = CartSummary()
    return _carts[session_id]


def _save_cart(session_id: str, cart: CartSummary) -> None:
    """Guarda el carrito de una sesión."""
    _carts[session_id] = cart


# ===========================================
# TOOLS
# ===========================================

def add_to_cart(session_id: str, product_sku: str, quantity: int) -> dict:
    """
    Agrega un producto al carrito.

    Valida que el producto exista, tenga stock suficiente,
    y lo agrega o actualiza la cantidad si ya está en el carrito.

    Args:
        session_id: ID de la sesión (para identificar el carrito)
        product_sku: SKU del producto a agregar
        quantity: Cantidad a agregar (debe ser > 0)

    Returns:
        dict con:
        - status: 'added' | 'updated' | 'no_stock' | 'not_found' | 'error'
        - message: mensaje descriptivo
        - cart_summary: resumen del carrito actualizado
        - item_added: datos del item agregado
    """
    try:
        # Validar cantidad
        if quantity <= 0:
            return {
                "status": "error",
                "message": "La cantidad debe ser mayor a 0.",
            }

        # Buscar producto
        product = get_product_by_sku(product_sku)
        if product is None:
            return {
                "status": "not_found",
                "message": f"No encontré el producto con código {product_sku}.",
            }

        if not product.active:
            return {
                "status": "not_found",
                "message": f"El producto {product.product_name} no está disponible.",
            }

        # Obtener carrito actual
        cart = _get_cart(session_id)

        # Verificar si ya está en el carrito
        existing_item = None
        existing_idx = None
        for idx, item in enumerate(cart.items):
            if item.product_sku == product_sku:
                existing_item = item
                existing_idx = idx
                break

        # Calcular cantidad total
        current_qty = existing_item.quantity if existing_item else 0
        total_qty = current_qty + quantity

        # Verificar stock
        if total_qty > product.stock:
            available = product.stock - current_qty
            if available <= 0:
                return {
                    "status": "no_stock",
                    "message": f"No hay stock suficiente de {product.product_name}. Stock disponible: {product.stock}, ya tenés {current_qty} en el carrito.",
                    "available_stock": available,
                    "product_name": product.product_name,
                    "current_in_cart": current_qty,
                }
            return {
                "status": "no_stock",
                "message": f"Solo hay {available} unidad(es) disponible(s) de {product.product_name}.",
                "available_stock": available,
                "product_name": product.product_name,
                "current_in_cart": current_qty,
            }

        # Agregar o actualizar
        if existing_item:
            # Actualizar cantidad
            updated_item = CartItem(
                product_sku=product_sku,
                product_name=product.product_name,
                quantity=total_qty,
                unit_price=product.price_customer,
                currency=product.currency,
            )
            cart.items[existing_idx] = updated_item
            status = "updated"
            message = f"Actualizado: {total_qty}x {product.product_name}"
        else:
            # Agregar nuevo item
            new_item = CartItem(
                product_sku=product_sku,
                product_name=product.product_name,
                quantity=quantity,
                unit_price=product.price_customer,
                currency=product.currency,
            )
            cart.items.append(new_item)
            status = "added"
            message = f"Agregado: {quantity}x {product.product_name}"

        # Guardar carrito
        _save_cart(session_id, cart)

        logger.info(f"Cart updated for session {session_id}: {status} {product_sku} x{quantity}")

        return {
            "status": status,
            "message": message,
            "item_added": {
                "sku": product_sku,
                "name": product.product_name,
                "quantity": quantity if status == "added" else total_qty,
                "unit_price": float(product.price_customer),
                "subtotal": float(product.price_customer * (quantity if status == "added" else total_qty)),
            },
            "cart_summary": _format_cart_summary(cart),
        }

    except Exception as e:
        logger.error(f"Error adding to cart: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al agregar el producto al carrito.",
        }


def view_cart(session_id: str) -> dict:
    """
    Muestra el contenido actual del carrito.

    Args:
        session_id: ID de la sesión

    Returns:
        dict con:
        - status: 'has_items' | 'empty'
        - message: mensaje descriptivo
        - cart_summary: resumen del carrito
        - formatted_cart: texto formateado del carrito
    """
    try:
        cart = _get_cart(session_id)

        if cart.is_empty:
            return {
                "status": "empty",
                "message": "Tu carrito está vacío. ¿Qué producto te gustaría agregar?",
                "cart_summary": _format_cart_summary(cart),
                "formatted_cart": "El carrito está vacío.",
            }

        return {
            "status": "has_items",
            "message": f"Tenés {cart.total_items} producto(s) en tu carrito.",
            "cart_summary": _format_cart_summary(cart),
            "formatted_cart": cart.format_cart(),
        }

    except Exception as e:
        logger.error(f"Error viewing cart: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al mostrar el carrito.",
        }


def remove_from_cart(session_id: str, product_sku: str) -> dict:
    """
    Elimina un producto del carrito.

    Args:
        session_id: ID de la sesión
        product_sku: SKU del producto a eliminar

    Returns:
        dict con:
        - status: 'removed' | 'not_in_cart' | 'error'
        - message: mensaje descriptivo
        - cart_summary: resumen del carrito actualizado
    """
    try:
        cart = _get_cart(session_id)

        # Buscar item
        item_to_remove = None
        for idx, item in enumerate(cart.items):
            if item.product_sku == product_sku:
                item_to_remove = cart.items.pop(idx)
                break

        if item_to_remove is None:
            return {
                "status": "not_in_cart",
                "message": f"El producto {product_sku} no está en tu carrito.",
            }

        # Guardar carrito
        _save_cart(session_id, cart)

        logger.info(f"Removed {product_sku} from cart for session {session_id}")

        return {
            "status": "removed",
            "message": f"Eliminado: {item_to_remove.product_name}",
            "cart_summary": _format_cart_summary(cart),
        }

    except Exception as e:
        logger.error(f"Error removing from cart: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al eliminar el producto.",
        }


def update_cart_quantity(session_id: str, product_sku: str, new_quantity: int) -> dict:
    """
    Actualiza la cantidad de un producto en el carrito.

    Args:
        session_id: ID de la sesión
        product_sku: SKU del producto
        new_quantity: Nueva cantidad (0 para eliminar)

    Returns:
        dict con:
        - status: 'updated' | 'removed' | 'not_in_cart' | 'no_stock' | 'error'
        - message: mensaje descriptivo
        - cart_summary: resumen del carrito actualizado
    """
    try:
        # Si cantidad es 0, eliminar
        if new_quantity <= 0:
            return remove_from_cart(session_id, product_sku)

        cart = _get_cart(session_id)

        # Buscar item
        item_idx = None
        for idx, item in enumerate(cart.items):
            if item.product_sku == product_sku:
                item_idx = idx
                break

        if item_idx is None:
            return {
                "status": "not_in_cart",
                "message": f"El producto {product_sku} no está en tu carrito.",
            }

        # Verificar stock
        product = get_product_by_sku(product_sku)
        if product and new_quantity > product.stock:
            return {
                "status": "no_stock",
                "message": f"Solo hay {product.stock} unidad(es) disponible(s) de {product.product_name}.",
                "available_stock": product.stock,
            }

        # Actualizar cantidad
        old_item = cart.items[item_idx]
        updated_item = CartItem(
            product_sku=old_item.product_sku,
            product_name=old_item.product_name,
            quantity=new_quantity,
            unit_price=old_item.unit_price,
            currency=old_item.currency,
        )
        cart.items[item_idx] = updated_item

        # Guardar carrito
        _save_cart(session_id, cart)

        logger.info(f"Updated {product_sku} to qty {new_quantity} for session {session_id}")

        return {
            "status": "updated",
            "message": f"Actualizado: {new_quantity}x {old_item.product_name}",
            "cart_summary": _format_cart_summary(cart),
        }

    except Exception as e:
        logger.error(f"Error updating cart quantity: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al actualizar la cantidad.",
        }


def clear_cart(session_id: str) -> dict:
    """
    Vacía completamente el carrito.

    Args:
        session_id: ID de la sesión

    Returns:
        dict con:
        - status: 'cleared' | 'already_empty'
        - message: mensaje descriptivo
    """
    try:
        cart = _get_cart(session_id)

        if cart.is_empty:
            return {
                "status": "already_empty",
                "message": "El carrito ya está vacío.",
            }

        items_count = cart.total_items
        _carts[session_id] = CartSummary()

        logger.info(f"Cleared cart for session {session_id} ({items_count} items)")

        return {
            "status": "cleared",
            "message": f"Carrito vaciado. Se eliminaron {items_count} producto(s).",
        }

    except Exception as e:
        logger.error(f"Error clearing cart: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al vaciar el carrito.",
        }


def get_cart_for_order(session_id: str) -> Optional[CartSummary]:
    """
    Obtiene el carrito para crear un pedido.
    Uso interno, no es una tool del agente.
    """
    cart = _get_cart(session_id)
    return cart if not cart.is_empty else None


def _format_cart_summary(cart: CartSummary) -> dict:
    """Formatea el resumen del carrito para el agente."""
    return {
        "items": [
            {
                "sku": item.product_sku,
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "subtotal": float(item.subtotal),
            }
            for item in cart.items
        ],
        "total_items": cart.total_items,
        "total_amount": float(cart.total_amount),
        "currency": cart.currency,
        "is_empty": cart.is_empty,
    }
