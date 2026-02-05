"""
email_service.py
Servicio de envío de emails operativos via SendGrid.
Si no hay API key configurada, solo loguea.
"""

import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_ops_email(
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> bool:
    """
    Envía email operativo al equipo de OPS.

    Si SendGrid no está configurado, solo loguea el mensaje.

    Args:
        subject: Asunto del email
        body_text: Cuerpo en texto plano
        body_html: Cuerpo en HTML (opcional)

    Returns:
        True si se envió correctamente, False si falló
    """
    settings = get_settings()

    if not settings.has_sendgrid():
        logger.warning(
            f"SendGrid not configured. Would send email:\n"
            f"  To: {settings.ops_email}\n"
            f"  Subject: {subject}\n"
            f"  Body: {body_text[:200]}..."
        )
        return True  # No es un error, simplemente no está configurado

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Content

        message = Mail(
            from_email=settings.from_email,
            to_emails=settings.ops_email,
            subject=subject,
        )

        # Agregar contenido
        message.add_content(Content("text/plain", body_text))
        if body_html:
            message.add_content(Content("text/html", body_html))

        # Enviar
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        response = sg.send(message)

        if response.status_code in (200, 201, 202):
            logger.info(f"Sent ops email: {subject}")
            return True
        else:
            logger.error(f"SendGrid error: status {response.status_code}")
            return False

    except ImportError:
        logger.error("SendGrid library not installed")
        return False
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False


def send_order_created_notification(
    order_id: str,
    vet_name: str,
    customer_name: str,
    total_amount: str,
    items_summary: str,
) -> bool:
    """Envía notificación de pedido creado."""
    subject = f"[DTV] Nuevo pedido {order_id} - {vet_name}"

    body_text = f"""
Nuevo pedido creado en Direct to Vet

Pedido: {order_id}
Veterinaria: {vet_name}
Cliente: {customer_name}
Total: {total_amount}

Items:
{items_summary}

---
Este es un email automático de Direct to Vet.
"""

    body_html = f"""
<h2>Nuevo pedido creado en Direct to Vet</h2>

<table style="border-collapse: collapse; margin: 20px 0;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Pedido</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{order_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Veterinaria</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{vet_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Cliente</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{customer_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Total</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>{total_amount}</strong></td>
    </tr>
</table>

<h3>Items</h3>
<pre>{items_summary}</pre>

<hr>
<p style="color: #888; font-size: 12px;">
    Este es un email automático de Direct to Vet.
</p>
"""

    return send_ops_email(subject, body_text, body_html)


def send_payment_approved_notification(
    order_id: str,
    vet_name: str,
    customer_name: str,
    total_amount: str,
    payment_id: str,
) -> bool:
    """Envía notificación de pago aprobado."""
    subject = f"[DTV] Pago aprobado {order_id} - {vet_name}"

    body_text = f"""
Pago APROBADO en Direct to Vet

Pedido: {order_id}
Veterinaria: {vet_name}
Cliente: {customer_name}
Total: {total_amount}
ID de Pago MP: {payment_id}

El pago fue acreditado en la cuenta de Mercado Pago de la veterinaria.

---
Este es un email automático de Direct to Vet.
"""

    body_html = f"""
<h2 style="color: #28a745;">✓ Pago APROBADO en Direct to Vet</h2>

<table style="border-collapse: collapse; margin: 20px 0;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Pedido</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{order_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Veterinaria</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{vet_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Cliente</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{customer_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Total</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd; color: #28a745;"><strong>{total_amount}</strong></td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>ID Pago MP</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{payment_id}</td>
    </tr>
</table>

<p>El pago fue acreditado en la cuenta de Mercado Pago de la veterinaria.</p>

<hr>
<p style="color: #888; font-size: 12px;">
    Este es un email automático de Direct to Vet.
</p>
"""

    return send_ops_email(subject, body_text, body_html)
