"""
catalog.py
Tool para buscar productos en el catálogo.
"""

import logging
from typing import Optional

from app.infra.sheets import search_products as db_search_products, get_product_by_sku
from app.models.schemas import Product

logger = logging.getLogger(__name__)


def search_catalog(vet_id: str, query: str, limit: int = 10) -> dict:
    """
    Busca productos en el catálogo por nombre o descripción.

    Solo devuelve productos con stock disponible.
    El catálogo es compartido entre todas las veterinarias (por ahora).

    Args:
        vet_id: ID de la veterinaria (para futura personalización de precios)
        query: Texto a buscar en nombre/descripción del producto
        limit: Máximo de resultados a devolver (default 10)

    Returns:
        dict con:
        - status: 'found' | 'empty' | 'error'
        - products: lista de productos encontrados
        - message: mensaje descriptivo
        - total_found: cantidad total de resultados
    """
    try:
        if not query or len(query.strip()) < 2:
            return {
                "status": "error",
                "message": "Por favor, indicá qué producto estás buscando.",
                "products": [],
                "total_found": 0,
            }

        # Buscar productos
        products = db_search_products(query=query.strip(), vet_id=vet_id)

        if not products:
            logger.info(f"No products found for query: {query}")
            return {
                "status": "empty",
                "message": f"No encontré productos que coincidan con '{query}'. Probá con otro término.",
                "products": [],
                "total_found": 0,
            }

        # Limitar resultados
        total_found = len(products)
        products = products[:limit]

        # Formatear para el agente
        formatted_products = [
            _format_product(p) for p in products
        ]

        logger.info(f"Found {total_found} products for query: {query}")
        return {
            "status": "found",
            "message": f"Encontré {total_found} producto(s) para '{query}'.",
            "products": formatted_products,
            "total_found": total_found,
            "showing": len(formatted_products),
        }

    except Exception as e:
        logger.error(f"Error searching catalog: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al buscar en el catálogo. Intentá de nuevo.",
            "products": [],
            "total_found": 0,
        }


def get_product_details(sku: str) -> dict:
    """
    Obtiene los detalles de un producto por SKU.

    Args:
        sku: Código SKU del producto

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'no_stock' | 'error'
        - product: datos del producto (si found)
        - message: mensaje descriptivo
    """
    try:
        product = get_product_by_sku(sku)

        if product is None:
            return {
                "status": "not_found",
                "message": f"No encontré el producto con código {sku}.",
            }

        if not product.active:
            return {
                "status": "not_found",
                "message": f"El producto {sku} no está disponible actualmente.",
            }

        if not product.has_stock:
            return {
                "status": "no_stock",
                "message": f"El producto {product.product_name} no tiene stock disponible.",
                "product": _format_product(product),
            }

        return {
            "status": "found",
            "message": f"Producto encontrado: {product.product_name}",
            "product": _format_product(product),
        }

    except Exception as e:
        logger.error(f"Error getting product details: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al obtener los detalles del producto.",
        }


def _format_product(product: Product) -> dict:
    """Formatea un producto para el agente."""
    return {
        "sku": product.sku,
        "name": product.product_name,
        "presentation": product.presentation,
        "description": product.description,
        "price": float(product.price_customer),
        "price_formatted": product.format_price(),
        "currency": product.currency,
        "stock": product.stock,
        "has_stock": product.has_stock,
    }
