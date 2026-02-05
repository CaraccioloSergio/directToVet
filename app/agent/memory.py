"""
memory.py
Gestión de memoria de sesión para el agente.
En PoC: memoria en RAM. En prod: Redis/Firestore.
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """Estados del flujo conversacional (FSM simple)."""

    IDLE = "IDLE"
    BROWSING_CATALOG = "BROWSING_CATALOG"
    BUILDING_CART = "BUILDING_CART"
    CHECKOUT_CUSTOMER = "CHECKOUT_CUSTOMER"
    CHECKOUT_DELIVERY = "CHECKOUT_DELIVERY"
    CONFIRMATION = "CONFIRMATION"
    COMPLETED = "COMPLETED"


@dataclass
class SessionMemory:
    """Memoria de una sesión de conversación."""

    session_id: str
    phone_e164: str
    vet_id: Optional[str] = None
    vet_name: Optional[str] = None
    mp_connected: bool = False

    # Estado del flujo
    state: ConversationState = ConversationState.IDLE

    # Datos del pedido en construcción
    current_order: Dict[str, Any] = field(default_factory=dict)

    # Último producto mencionado (para referencias como "agregame 2")
    last_product_sku: Optional[str] = None
    last_product_name: Optional[str] = None

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def update_activity(self):
        """Actualiza el timestamp de última actividad."""
        self.last_activity = datetime.utcnow()

    def set_vet(self, vet_id: str, name: str, mp_connected: bool):
        """Guarda los datos del veterinario identificado."""
        self.vet_id = vet_id
        self.vet_name = name
        self.mp_connected = mp_connected

    def set_last_product(self, sku: str, name: str):
        """Guarda el último producto mencionado."""
        self.last_product_sku = sku
        self.last_product_name = name

    def is_identified(self) -> bool:
        """Verifica si el veterinario está identificado."""
        return self.vet_id is not None

    def to_context_dict(self) -> dict:
        """Convierte a diccionario para usar en prompts."""
        return {
            "session_id": self.session_id,
            "phone": self.phone_e164,
            "vet_id": self.vet_id,
            "name": self.vet_name,
            "mp_connected": self.mp_connected,
            "state": self.state.value,
            "last_product": self.last_product_name,
        }


class SessionStore:
    """
    Almacén de sesiones en memoria.

    En producción esto debería ser Redis o Firestore
    para persistencia y escalabilidad.
    """

    def __init__(self, ttl_minutes: int = 60):
        """
        Inicializa el store.

        Args:
            ttl_minutes: Tiempo de vida de sesiones inactivas
        """
        self._sessions: Dict[str, SessionMemory] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    def get_or_create(self, session_id: str, phone_e164: str) -> SessionMemory:
        """
        Obtiene una sesión existente o crea una nueva.

        Args:
            session_id: ID único de la sesión
            phone_e164: Número de teléfono del usuario

        Returns:
            Memoria de la sesión
        """
        # Limpiar sesiones expiradas ocasionalmente
        self._cleanup_expired()

        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.update_activity()
            return session

        # Crear nueva sesión
        session = SessionMemory(
            session_id=session_id,
            phone_e164=phone_e164,
        )
        self._sessions[session_id] = session

        logger.info(f"Created new session: {session_id} for {phone_e164}")

        return session

    def get(self, session_id: str) -> Optional[SessionMemory]:
        """
        Obtiene una sesión por ID.

        Args:
            session_id: ID de la sesión

        Returns:
            Memoria de la sesión o None si no existe
        """
        session = self._sessions.get(session_id)
        if session:
            session.update_activity()
        return session

    def get_by_phone(self, phone_e164: str) -> Optional[SessionMemory]:
        """
        Busca una sesión por número de teléfono.

        Args:
            phone_e164: Número de teléfono

        Returns:
            Sesión más reciente para ese número o None
        """
        matching = [
            s for s in self._sessions.values() if s.phone_e164 == phone_e164
        ]

        if not matching:
            return None

        # Devolver la más reciente
        return max(matching, key=lambda s: s.last_activity)

    def delete(self, session_id: str) -> bool:
        """
        Elimina una sesión.

        Args:
            session_id: ID de la sesión

        Returns:
            True si se eliminó, False si no existía
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Deleted session: {session_id}")
            return True
        return False

    def _cleanup_expired(self):
        """Elimina sesiones expiradas."""
        now = datetime.utcnow()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_activity > self._ttl
        ]

        for sid in expired:
            del self._sessions[sid]

        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")


# Singleton global
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Obtiene el store de sesiones (singleton)."""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


def generate_session_id(phone_e164: str) -> str:
    """
    Genera un session_id basado en el teléfono.

    Para WhatsApp, usamos el número como session_id
    ya que cada conversación es única por número.

    Args:
        phone_e164: Número de teléfono

    Returns:
        Session ID
    """
    # Limpiar el número
    clean_phone = phone_e164.replace("+", "").replace(" ", "").replace("-", "")
    return f"wa_{clean_phone}"
