"""
admin.py
Tools de administración — solo disponibles desde el backoffice, nunca desde WhatsApp.
"""

import logging
from app.infra.sheets import (
    get_product_by_sku,
    update_product_price as sheets_update_product_price,
    update_shipping_zone_price as sheets_update_shipping_zone_price,
    get_all_shipping_zones,
)

logger = logging.getLogger(__name__)


def update_product_price(sku: str, new_price_customer: float, new_price_distributor: float = None) -> dict:
    """
    Actualiza el precio de un producto en el catálogo.

    Args:
        sku: SKU del producto a actualizar.
        new_price_customer: Nuevo precio de venta al cliente (ARS).
        new_price_distributor: Nuevo precio de distribuidora (ARS). Opcional.

    Returns:
        dict con status ('updated' o 'not_found' o 'error') y mensaje.
    """
    try:
        product = get_product_by_sku(sku)
        if not product:
            return {"status": "not_found", "message": f"No existe producto con SKU '{sku}'."}

        ok = sheets_update_product_price(sku, new_price_customer, new_price_distributor)
        if not ok:
            return {"status": "error", "message": "No se pudo actualizar el precio."}

        msg = f"Precio de {product.product_name} actualizado a ${new_price_customer:,.0f}"
        if new_price_distributor is not None:
            msg += f" (distribuidora: ${new_price_distributor:,.0f})"
        return {"status": "updated", "sku": sku, "product_name": product.product_name, "message": msg}

    except Exception as e:
        logger.error(f"Error in update_product_price tool: {e}")
        return {"status": "error", "message": str(e)}


def update_shipping_cost(zone: str, new_price: float) -> dict:
    """
    Actualiza el costo de envío de una zona AMBA.

    Args:
        zone: Nombre de la zona (ej: 'CABA', 'San Isidro', 'La Plata').
        new_price: Nuevo costo de envío en ARS.

    Returns:
        dict con status ('updated' o 'not_found' o 'error') y mensaje.
    """
    try:
        ok = sheets_update_shipping_zone_price(zone, new_price)
        if not ok:
            zones = get_all_shipping_zones()
            available = ", ".join(z["zone"] for z in zones[:10])
            return {
                "status": "not_found",
                "message": f"Zona '{zone}' no encontrada. Zonas disponibles: {available}",
            }
        return {
            "status": "updated",
            "zone": zone,
            "message": f"Costo de envío para {zone} actualizado a ${new_price:,.0f}",
        }
    except Exception as e:
        logger.error(f"Error in update_shipping_cost tool: {e}")
        return {"status": "error", "message": str(e)}
