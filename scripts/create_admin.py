"""
create_admin.py
Script para crear el primer usuario admin del backoffice.

Uso local (genera .users.json):
    python scripts/create_admin.py

Uso producción (sube a Secret Manager):
    python scripts/create_admin.py --production --project-id yopdev-prod
"""
import argparse
import getpass
import json
import os
import secrets
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt


def main():
    parser = argparse.ArgumentParser(description="Crear usuario admin para el backoffice")
    parser.add_argument("--production", action="store_true", help="Subir a Secret Manager")
    parser.add_argument("--project-id", default="", help="GCP Project ID")
    args = parser.parse_args()

    print("=== Crear usuario admin — DirectToVet Backoffice ===\n")
    username = input("Username: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password (min 8 chars): ")
    password2 = getpass.getpass("Confirmar password: ")

    if password != password2:
        print("ERROR: Las contraseñas no coinciden.")
        sys.exit(1)
    if len(password) < 8:
        print("ERROR: La contraseña debe tener al menos 8 caracteres.")
        sys.exit(1)

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    user = {
        "user_id": f"USR-{secrets.token_hex(4).upper()}",
        "username": username,
        "email": email,
        "password_hash": password_hash,
        "role": "admin",
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login": None,
    }
    payload = json.dumps({"users": [user]}, indent=2).encode()

    if args.production:
        project_id = args.project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or input("GCP Project ID: ").strip()
        secret_id = "dtv-backoffice-users"
        secret_name = f"projects/{project_id}/secrets/{secret_id}"
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            # Intentar agregar versión (el secret ya existe)
            try:
                client.add_secret_version(request={"parent": secret_name, "payload": {"data": payload}})
            except Exception:
                # El secret no existe aún — crearlo primero
                client.create_secret(request={
                    "parent": f"projects/{project_id}",
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                })
                client.add_secret_version(request={"parent": secret_name, "payload": {"data": payload}})
            print(f"\n✓ Usuario '{username}' guardado en Secret Manager ({secret_name}).")
            print("  No olvides dar acceso al service account: roles/secretmanager.secretAccessor")
        except Exception as e:
            print(f"ERROR: {e}")
            sys.exit(1)
    else:
        with open(".users.json", "wb") as f:
            f.write(payload)
        print(f"\n✓ Usuario '{username}' guardado en .users.json (local).")
        print("  .users.json ya está en .gitignore — no se va a commitear.")

    print(f"\nJWT_SECRET_KEY para generar:")
    print(f"  {secrets.token_urlsafe(32)}")
    print("  Copiá este valor y configuralo como variable de entorno en Cloud Run.")


if __name__ == "__main__":
    main()
