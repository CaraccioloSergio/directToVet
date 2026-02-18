"""
templates/__init__.py
Utilidades para renderizar templates HTML.
"""

import os
from pathlib import Path

# Ruta a la carpeta de templates
TEMPLATES_DIR = Path(__file__).parent


def render_template(template_name: str, **kwargs) -> str:
    """
    Renderiza un template HTML con variables.

    Args:
        template_name: Nombre del archivo de template (ej: "oauth_success.html")
        **kwargs: Variables para reemplazar en el template (formato {{variable}})

    Returns:
        HTML renderizado como string
    """
    template_path = TEMPLATES_DIR / template_name

    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Reemplazar variables {{key}} con valores
    for key, value in kwargs.items():
        placeholder = "{{" + key + "}}"
        html = html.replace(placeholder, str(value) if value else "")

    return html


def get_oauth_success_html(whatsapp_number: str = "") -> str:
    """Retorna HTML para OAuth exitoso."""
    return render_template("oauth_success.html", whatsapp_number=whatsapp_number)


def get_oauth_error_html(error_message: str = "Error desconocido") -> str:
    """Retorna HTML para error de OAuth."""
    return render_template("oauth_error.html", error_message=error_message)


def get_payment_success_html(order_id: str = "", amount: str = "", whatsapp_number: str = "") -> str:
    """Retorna HTML para pago exitoso."""
    return render_template(
        "payment_success.html",
        order_id=order_id,
        amount=amount,
        whatsapp_number=whatsapp_number,
    )


def get_payment_pending_html(order_id: str = "", amount: str = "", whatsapp_number: str = "") -> str:
    """Retorna HTML para pago pendiente."""
    return render_template(
        "payment_pending.html",
        order_id=order_id,
        amount=amount,
        whatsapp_number=whatsapp_number,
    )


def get_payment_error_html(error_message: str = "El pago fue rechazado") -> str:
    """Retorna HTML para error de pago."""
    return render_template("payment_error.html", error_message=error_message)


def get_test_console_html() -> str:
    """Retorna HTML para la consola de testing."""
    return render_template("test_console.html")
