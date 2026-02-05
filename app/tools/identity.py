"""
identity.py
Tool para identificar veterinarias y clientes por teléfono.
"""

import logging
from enum import Enum
from typing import Optional

import phonenumbers

from app.infra.sheets import get_vet_by_phone, get_customer_by_phone_global
from app.models.schemas import VetContext

logger = logging.getLogger(__name__)


# =============================================================================
# ROLES
# =============================================================================


class UserRole(str, Enum):
    """Rol del usuario en la conversación."""
    VET = "VET"           # Veterinaria registrada
    CUSTOMER = "CUSTOMER" # Cliente final
    UNKNOWN = "UNKNOWN"   # No registrado


def identify_role(phone_e164: str) -> dict:
    """
    Identifica el rol del usuario por su número de WhatsApp.

    Orden de búsqueda:
    1. Busca en veterinarias (vets sheet)
    2. Si no es vet, busca en clientes (customers sheet)
    3. Si no está en ninguno, retorna UNKNOWN

    Args:
        phone_e164: Número de teléfono en formato E.164

    Returns:
        dict con:
        - role: 'VET' | 'CUSTOMER' | 'UNKNOWN'
        - vet_context: datos de la vet (si role=VET)
        - customer: datos del cliente (si role=CUSTOMER)
        - message: mensaje descriptivo
    """
    try:
        # Normalizar teléfono
        normalized_phone = _normalize_phone(phone_e164)
        if not normalized_phone:
            return {
                "role": UserRole.UNKNOWN,
                "message": f"Número de teléfono inválido: {phone_e164}",
            }

        # 1. Buscar en veterinarias
        vet = get_vet_by_phone(normalized_phone)
        if vet is not None:
            if not vet.active:
                logger.warning(f"Veterinaria inactiva: {vet.vet_id}")
                return {
                    "role": UserRole.UNKNOWN,
                    "message": f"La veterinaria {vet.name} está temporalmente inactiva.",
                    "vet_id": vet.vet_id,
                }

            logger.info(f"Rol identificado: VET - {vet.vet_id}")
            return {
                "role": UserRole.VET,
                "message": f"Veterinaria identificada: {vet.name}",
                "vet_context": {
                    "vet_id": vet.vet_id,
                    "name": vet.name,
                    "whatsapp_e164": vet.whatsapp_e164,
                    "mp_connected": vet.mp_connected,
                    "mp_user_id": vet.mp_user_id,
                    "contact_name": vet.contact_name,
                    "address": vet.address,
                    "email": vet.email,
                    "distributor_id": vet.distributor_id,
                },
            }

        # 2. Buscar en clientes
        customer = get_customer_by_phone_global(normalized_phone)
        if customer is not None:
            logger.info(f"Rol identificado: CUSTOMER - {customer.customer_id}")
            return {
                "role": UserRole.CUSTOMER,
                "message": f"Cliente identificado: {customer.full_name}",
                "customer": {
                    "customer_id": customer.customer_id,
                    "vet_id": customer.vet_id,
                    "name": customer.full_name,
                    "email": customer.email,
                    "whatsapp_e164": customer.whatsapp_e164,
                },
            }

        # 3. No registrado
        logger.info(f"Rol no identificado para: {normalized_phone}")
        return {
            "role": UserRole.UNKNOWN,
            "message": "Número no registrado en el sistema.",
        }

    except Exception as e:
        logger.error(f"Error identificando rol: {e}")
        return {
            "role": UserRole.UNKNOWN,
            "message": "Error al identificar usuario.",
        }


def identify_veterinarian(phone_e164: str) -> dict:
    """
    Identifica una veterinaria por su número de WhatsApp.

    Esta tool busca en la base de datos de veterinarias registradas
    y devuelve el contexto si encuentra una coincidencia activa.

    Args:
        phone_e164: Número de teléfono en formato E.164 (ej: +5491155551234)

    Returns:
        dict con:
        - status: 'found' | 'not_found' | 'inactive' | 'error'
        - vet_context: datos de la veterinaria (si found)
        - message: mensaje descriptivo
    """
    try:
        # Normalizar el teléfono a E.164
        normalized_phone = _normalize_phone(phone_e164)
        if not normalized_phone:
            return {
                "status": "error",
                "message": f"Número de teléfono inválido: {phone_e164}",
            }

        # Buscar en la base de datos
        vet = get_vet_by_phone(normalized_phone)

        if vet is None:
            logger.info(f"Veterinaria no encontrada para teléfono: {normalized_phone}")
            return {
                "status": "not_found",
                "message": "No hay ninguna veterinaria registrada con este número de WhatsApp.",
            }

        if not vet.active:
            logger.warning(f"Veterinaria inactiva: {vet.vet_id}")
            return {
                "status": "inactive",
                "message": f"La veterinaria {vet.name} está temporalmente inactiva.",
                "vet_id": vet.vet_id,
            }

        logger.info(f"Veterinaria identificada: {vet.vet_id} - {vet.name}")
        return {
            "status": "found",
            "message": f"Veterinaria identificada: {vet.name}",
            "vet_context": {
                "vet_id": vet.vet_id,
                "name": vet.name,
                "whatsapp_e164": vet.whatsapp_e164,
                "mp_connected": vet.mp_connected,
                "mp_user_id": vet.mp_user_id,
            },
        }

    except Exception as e:
        logger.error(f"Error identificando veterinaria: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al verificar tu identidad. Por favor, intentá de nuevo.",
        }


def _normalize_phone(phone: str) -> Optional[str]:
    """
    Normaliza un número de teléfono a formato E.164.

    Acepta varios formatos:
    - +5491155551234 (ya E.164)
    - 5491155551234 (sin +)
    - 1155551234 (solo número argentino)
    - 011 5555-1234 (con código de área)

    Returns:
        Teléfono en formato E.164 o None si es inválido
    """
    # Limpiar caracteres no numéricos excepto +
    cleaned = "".join(c for c in phone if c.isdigit() or c == "+")

    # Si no empieza con +, intentar agregar código de Argentina
    if not cleaned.startswith("+"):
        # Si empieza con 54, agregar solo +
        if cleaned.startswith("54"):
            cleaned = f"+{cleaned}"
        # Si es un número local argentino (10-11 dígitos)
        elif len(cleaned) >= 10 and len(cleaned) <= 11:
            cleaned = f"+54{cleaned}"
        else:
            cleaned = f"+{cleaned}"

    try:
        parsed = phonenumbers.parse(cleaned, "AR")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass

    return None
