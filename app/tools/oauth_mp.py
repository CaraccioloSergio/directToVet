"""
oauth_mp.py
Tools para gestionar OAuth con Mercado Pago.
Permite que cada veterinaria conecte su cuenta MP.
"""

import logging
import time
from typing import Optional
from datetime import datetime, timedelta

import httpx

from app.config import get_settings
from app.infra.token_store import get_token_store
from app.infra.sheets import update_vet_mp_status
from app.models.schemas import StoredToken

logger = logging.getLogger(__name__)
settings = get_settings()


# Helper functions to work with token store
def save_mp_tokens(vet_id: str, token: StoredToken) -> bool:
    """Guarda tokens de MP usando el token store."""
    store = get_token_store()
    return store.save_token(token)


def get_mp_tokens(vet_id: str) -> Optional[StoredToken]:
    """Obtiene tokens de MP del token store."""
    store = get_token_store()
    return store.get_token(vet_id)


def start_mp_oauth(vet_id: str) -> dict:
    """
    Inicia el flujo OAuth de Mercado Pago para una veterinaria.

    Genera la URL de autorización que el veterinario debe visitar
    para conectar su cuenta de Mercado Pago.

    Args:
        vet_id: ID de la veterinaria

    Returns:
        dict con:
        - status: 'success' | 'error' | 'not_configured'
        - message: mensaje descriptivo
        - redirect_url: URL para que el vet autorice (si success)
        - instructions: instrucciones para el usuario
    """
    try:
        # Verificar que MP esté configurado
        if not settings.has_mp():
            logger.warning("Mercado Pago credentials not configured")
            return {
                "status": "not_configured",
                "message": "Mercado Pago no está configurado en el sistema.",
            }

        # Generar state con vet_id (para identificar en callback)
        # El state incluye vet_id y timestamp para validación
        state = f"{vet_id}|{int(time.time())}"

        # Generar URL de autorización
        redirect_url = settings.get_mp_oauth_url(state)

        logger.info(f"Generated MP OAuth URL for vet {vet_id}")

        return {
            "status": "success",
            "message": "URL de autorización generada.",
            "redirect_url": redirect_url,
            "instructions": (
                "Para conectar tu cuenta de Mercado Pago:\n"
                "1. Abrí el siguiente link en tu navegador\n"
                "2. Iniciá sesión con tu cuenta de Mercado Pago\n"
                "3. Autorizá la conexión\n"
                "4. Vas a ser redirigido de vuelta automáticamente"
            ),
        }

    except Exception as e:
        logger.error(f"Error starting MP OAuth: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al generar el link de autorización.",
        }


def complete_mp_oauth(vet_id: str, code: str) -> dict:
    """
    Completa el flujo OAuth canjeando el código por tokens.

    Este método es llamado por el callback endpoint cuando MP
    redirige de vuelta con el código de autorización.

    Args:
        vet_id: ID de la veterinaria
        code: Código de autorización de MP (ej: TG-123456...)

    Returns:
        dict con:
        - status: 'success' | 'error' | 'invalid_code'
        - message: mensaje descriptivo
        - mp_user_id: ID del usuario en MP (si success)
    """
    try:
        if not settings.has_mp():
            return {
                "status": "error",
                "message": "Mercado Pago no está configurado.",
            }

        # Intercambiar código por tokens
        token_data = _exchange_code_for_tokens(code)

        if token_data is None:
            return {
                "status": "invalid_code",
                "message": "El código de autorización es inválido o expiró.",
            }

        # Guardar tokens
        tokens = StoredToken(
            vet_id=vet_id,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=datetime.utcnow() + timedelta(seconds=token_data["expires_in"]),
            mp_user_id=str(token_data["user_id"]),
        )

        if not save_mp_tokens(vet_id, tokens):
            return {
                "status": "error",
                "message": "Error al guardar las credenciales.",
            }

        # Actualizar estado de conexión en Sheets
        update_vet_mp_status(
            vet_id=vet_id,
            mp_connected=True,
            mp_user_id=str(token_data["user_id"]),
        )

        logger.info(f"MP OAuth completed for vet {vet_id}, MP user {token_data['user_id']}")

        return {
            "status": "success",
            "message": "¡Cuenta de Mercado Pago conectada exitosamente!",
            "mp_user_id": str(token_data["user_id"]),
        }

    except Exception as e:
        logger.error(f"Error completing MP OAuth: {e}")
        return {
            "status": "error",
            "message": "Hubo un problema al conectar la cuenta de Mercado Pago.",
        }


def ensure_valid_mp_token(vet_id: str) -> dict:
    """
    Obtiene un access_token válido para la veterinaria.

    Si el token está por expirar o ya expiró, intenta renovarlo
    automáticamente usando el refresh_token.

    Args:
        vet_id: ID de la veterinaria

    Returns:
        dict con:
        - status: 'success' | 'not_connected' | 'refresh_failed' | 'error'
        - message: mensaje descriptivo
        - access_token: token válido (si success)
    """
    try:
        # Obtener tokens guardados
        tokens = get_mp_tokens(vet_id)

        if tokens is None:
            return {
                "status": "not_connected",
                "message": "Esta veterinaria no tiene Mercado Pago conectado.",
            }

        # Verificar si necesita refresh (5 minutos de margen)
        margin = timedelta(minutes=5)
        if tokens.expires_at - margin > datetime.utcnow():
            # Token aún válido
            return {
                "status": "success",
                "message": "Token válido.",
                "access_token": tokens.access_token,
            }

        # Necesita refresh
        logger.info(f"Refreshing MP token for vet {vet_id}")
        new_tokens = _refresh_mp_token(tokens.refresh_token)

        if new_tokens is None:
            return {
                "status": "refresh_failed",
                "message": "No se pudo renovar el token. El veterinario debe volver a conectar su cuenta.",
            }

        # Guardar nuevos tokens
        updated = StoredToken(
            vet_id=vet_id,
            access_token=new_tokens["access_token"],
            refresh_token=new_tokens.get("refresh_token", tokens.refresh_token),
            expires_at=datetime.utcnow() + timedelta(seconds=new_tokens["expires_in"]),
            mp_user_id=tokens.mp_user_id,
        )

        save_mp_tokens(vet_id, updated)

        return {
            "status": "success",
            "message": "Token renovado.",
            "access_token": updated.access_token,
        }

    except Exception as e:
        logger.error(f"Error ensuring valid MP token: {e}")
        return {
            "status": "error",
            "message": "Error al obtener credenciales de Mercado Pago.",
        }


def check_mp_connection(vet_id: str) -> dict:
    """
    Verifica si una veterinaria tiene MP conectado y el estado del token.

    Args:
        vet_id: ID de la veterinaria

    Returns:
        dict con:
        - status: 'connected' | 'not_connected' | 'expired' | 'error'
        - message: mensaje descriptivo
        - mp_user_id: ID de usuario MP (si connected)
    """
    try:
        tokens = get_mp_tokens(vet_id)

        if tokens is None:
            return {
                "status": "not_connected",
                "message": "Mercado Pago no está conectado.",
            }

        # Verificar expiración
        if tokens.expires_at < datetime.utcnow():
            return {
                "status": "expired",
                "message": "El token de Mercado Pago expiró. Intentá renovarlo.",
                "mp_user_id": tokens.mp_user_id,
            }

        return {
            "status": "connected",
            "message": "Mercado Pago está conectado y activo.",
            "mp_user_id": tokens.mp_user_id,
        }

    except Exception as e:
        logger.error(f"Error checking MP connection: {e}")
        return {
            "status": "error",
            "message": "Error al verificar la conexión de Mercado Pago.",
        }


# =============================================================================
# Funciones internas (no son tools)
# =============================================================================

def _exchange_code_for_tokens(code: str) -> Optional[dict]:
    """
    Intercambia el código de autorización por tokens.

    POST https://api.mercadopago.com/oauth/token
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                settings.mp_token_url,
                data={
                    "client_id": settings.mp_client_id,
                    "client_secret": settings.mp_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.mp_redirect_uri,
                },
            )

            if response.status_code == 200:
                return response.json()

            logger.error(f"MP token exchange failed: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"Error exchanging MP code: {e}")
        return None


def _refresh_mp_token(refresh_token: str) -> Optional[dict]:
    """
    Renueva un token usando el refresh_token.

    POST https://api.mercadopago.com/oauth/token
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                settings.mp_token_url,
                data={
                    "client_id": settings.mp_client_id,
                    "client_secret": settings.mp_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )

            if response.status_code == 200:
                return response.json()

            logger.error(f"MP token refresh failed: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"Error refreshing MP token: {e}")
        return None
