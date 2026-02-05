"""
token_store.py
Almacenamiento de tokens OAuth de Mercado Pago.
- En desarrollo: archivo JSON local
- En producción: Google Secret Manager
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional
import threading

from app.config import get_settings
from app.models.schemas import StoredToken

logger = logging.getLogger(__name__)


# ===========================================
# INTERFAZ ABSTRACTA
# ===========================================

class TokenStore(ABC):
    """Interfaz para almacenamiento de tokens."""

    @abstractmethod
    def get_token(self, vet_id: str) -> Optional[StoredToken]:
        """Obtiene el token de una veterinaria."""
        pass

    @abstractmethod
    def save_token(self, token: StoredToken) -> bool:
        """Guarda el token de una veterinaria."""
        pass

    @abstractmethod
    def delete_token(self, vet_id: str) -> bool:
        """Elimina el token de una veterinaria."""
        pass


# ===========================================
# LOCAL FILE STORE (DESARROLLO)
# ===========================================

class LocalTokenStore(TokenStore):
    """Almacenamiento de tokens en archivo JSON local."""

    def __init__(self, file_path: Optional[str] = None):
        settings = get_settings()
        self.file_path = Path(file_path or settings.local_token_store_path)
        self._lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Crea el archivo y directorio si no existen."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("{}")
            logger.info(f"Created token store file: {self.file_path}")

    def _read_all(self) -> dict:
        """Lee todos los tokens del archivo."""
        try:
            content = self.file_path.read_text()
            return json.loads(content) if content else {}
        except Exception as e:
            logger.error(f"Error reading token store: {e}")
            return {}

    def _write_all(self, data: dict) -> bool:
        """Escribe todos los tokens al archivo."""
        try:
            self.file_path.write_text(json.dumps(data, indent=2, default=str))
            return True
        except Exception as e:
            logger.error(f"Error writing token store: {e}")
            return False

    def get_token(self, vet_id: str) -> Optional[StoredToken]:
        """Obtiene el token de una veterinaria."""
        with self._lock:
            data = self._read_all()
            token_data = data.get(vet_id)

            if not token_data:
                return None

            try:
                return StoredToken(
                    vet_id=token_data["vet_id"],
                    access_token=token_data["access_token"],
                    refresh_token=token_data["refresh_token"],
                    expires_at=datetime.fromisoformat(token_data["expires_at"]),
                    mp_user_id=token_data["mp_user_id"],
                    updated_at=datetime.fromisoformat(token_data.get("updated_at", datetime.utcnow().isoformat())),
                )
            except Exception as e:
                logger.error(f"Error parsing token for {vet_id}: {e}")
                return None

    def save_token(self, token: StoredToken) -> bool:
        """Guarda el token de una veterinaria."""
        with self._lock:
            data = self._read_all()
            data[token.vet_id] = {
                "vet_id": token.vet_id,
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at.isoformat(),
                "mp_user_id": token.mp_user_id,
                "updated_at": token.updated_at.isoformat(),
            }
            success = self._write_all(data)
            if success:
                logger.info(f"Saved token for vet {token.vet_id}")
            return success

    def delete_token(self, vet_id: str) -> bool:
        """Elimina el token de una veterinaria."""
        with self._lock:
            data = self._read_all()
            if vet_id in data:
                del data[vet_id]
                success = self._write_all(data)
                if success:
                    logger.info(f"Deleted token for vet {vet_id}")
                return success
            return True  # Ya no existe


# ===========================================
# SECRET MANAGER STORE (PRODUCCIÓN)
# ===========================================

class SecretManagerTokenStore(TokenStore):
    """Almacenamiento de tokens en Google Secret Manager."""

    def __init__(self, project_id: Optional[str] = None):
        settings = get_settings()
        self.project_id = project_id or settings.gcp_project_id
        self._client = None

    def _get_client(self):
        """Obtiene cliente de Secret Manager (lazy)."""
        if self._client is None:
            try:
                from google.cloud import secretmanager
                self._client = secretmanager.SecretManagerServiceClient()
            except ImportError:
                raise RuntimeError("google-cloud-secret-manager not installed")
        return self._client

    def _secret_name(self, vet_id: str) -> str:
        """Genera el nombre del secret para una vet."""
        return f"projects/{self.project_id}/secrets/dtv-token-{vet_id}/versions/latest"

    def _secret_id(self, vet_id: str) -> str:
        """Genera el ID del secret para una vet."""
        return f"dtv-token-{vet_id}"

    def get_token(self, vet_id: str) -> Optional[StoredToken]:
        """Obtiene el token de una veterinaria desde Secret Manager."""
        try:
            client = self._get_client()
            name = self._secret_name(vet_id)
            response = client.access_secret_version(request={"name": name})
            token_data = json.loads(response.payload.data.decode("UTF-8"))

            return StoredToken(
                vet_id=token_data["vet_id"],
                access_token=token_data["access_token"],
                refresh_token=token_data["refresh_token"],
                expires_at=datetime.fromisoformat(token_data["expires_at"]),
                mp_user_id=token_data["mp_user_id"],
                updated_at=datetime.fromisoformat(token_data.get("updated_at", datetime.utcnow().isoformat())),
            )
        except Exception as e:
            logger.warning(f"Token not found for vet {vet_id}: {e}")
            return None

    def save_token(self, token: StoredToken) -> bool:
        """Guarda el token en Secret Manager."""
        try:
            client = self._get_client()
            parent = f"projects/{self.project_id}"
            secret_id = self._secret_id(token.vet_id)

            # Preparar datos
            token_data = json.dumps({
                "vet_id": token.vet_id,
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at.isoformat(),
                "mp_user_id": token.mp_user_id,
                "updated_at": token.updated_at.isoformat(),
            })

            # Intentar crear el secret (si no existe)
            try:
                client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except Exception:
                pass  # El secret ya existe

            # Agregar nueva versión
            client.add_secret_version(
                request={
                    "parent": f"{parent}/secrets/{secret_id}",
                    "payload": {"data": token_data.encode("UTF-8")},
                }
            )

            logger.info(f"Saved token for vet {token.vet_id} to Secret Manager")
            return True
        except Exception as e:
            logger.error(f"Error saving token to Secret Manager: {e}")
            return False

    def delete_token(self, vet_id: str) -> bool:
        """Elimina el token de Secret Manager."""
        try:
            client = self._get_client()
            parent = f"projects/{self.project_id}"
            secret_id = self._secret_id(vet_id)

            client.delete_secret(request={"name": f"{parent}/secrets/{secret_id}"})
            logger.info(f"Deleted token for vet {vet_id} from Secret Manager")
            return True
        except Exception as e:
            logger.error(f"Error deleting token from Secret Manager: {e}")
            return False


# ===========================================
# FACTORY
# ===========================================

_token_store: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    """
    Obtiene la instancia del token store apropiada según el entorno.
    Singleton.
    """
    global _token_store

    if _token_store is None:
        settings = get_settings()

        if settings.is_production and settings.gcp_project_id:
            logger.info("Using Secret Manager for token storage")
            _token_store = SecretManagerTokenStore()
        else:
            logger.info("Using local file for token storage")
            _token_store = LocalTokenStore()

    return _token_store
