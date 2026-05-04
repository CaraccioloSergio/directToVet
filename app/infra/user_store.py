"""
user_store.py
Gestión de usuarios del backoffice almacenados en Secret Manager (prod) o .users.json (dev).
"""
import json
import logging
import os
import secrets as _secrets
from datetime import datetime, timezone
from typing import Optional

import bcrypt

from app.config import get_settings

logger = logging.getLogger(__name__)

_SECRET_NAME = "dtv-backoffice-users"
_LOCAL_PATH = ".users.json"


def _get_project_id() -> str:
    s = get_settings()
    return s.gcp_project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")


def _load_users() -> list:
    s = get_settings()
    try:
        if s.is_production:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{_get_project_id()}/secrets/{_SECRET_NAME}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return json.loads(response.payload.data.decode()).get("users", [])
        else:
            if os.path.exists(_LOCAL_PATH):
                with open(_LOCAL_PATH) as f:
                    return json.load(f).get("users", [])
            return []
    except Exception as e:
        logger.error(f"Error loading users from store: {e}")
        return []


def _save_users(users: list) -> bool:
    s = get_settings()
    payload = json.dumps({"users": users}, indent=2, ensure_ascii=False).encode()
    try:
        if s.is_production:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{_get_project_id()}/secrets/{_SECRET_NAME}"
            client.add_secret_version(request={"parent": parent, "payload": {"data": payload}})
        else:
            with open(_LOCAL_PATH, "wb") as f:
                f.write(payload)
        return True
    except Exception as e:
        logger.error(f"Error saving users to store: {e}")
        return False


def get_user_by_username(username: str) -> Optional[dict]:
    for u in _load_users():
        if u.get("username") == username and u.get("active", True):
            return u
    return None


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(12)).decode()


def create_user(username: str, email: str, password: str, role: str = "admin") -> dict:
    users = _load_users()
    if any(u["username"] == username for u in users):
        return {"status": "error", "message": f"Usuario '{username}' ya existe"}
    user = {
        "user_id": f"USR-{_secrets.token_hex(4).upper()}",
        "username": username,
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login": None,
    }
    users.append(user)
    if not _save_users(users):
        return {"status": "error", "message": "Error al guardar el usuario"}
    return {"status": "created", "user_id": user["user_id"]}


def update_last_login(username: str):
    users = _load_users()
    for u in users:
        if u["username"] == username:
            u["last_login"] = datetime.now(timezone.utc).isoformat()
            break
    _save_users(users)


def deactivate_user(user_id: str) -> bool:
    users = _load_users()
    for u in users:
        if u["user_id"] == user_id:
            u["active"] = False
            return _save_users(users)
    return False


def list_users() -> list:
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in _load_users()]


def has_any_user() -> bool:
    return len(_load_users()) > 0
