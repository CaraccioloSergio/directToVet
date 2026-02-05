"""
main.py
Aplicación FastAPI principal de Direct to Vet.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse

from app.config import get_settings
from app.webhooks.twilio import router as twilio_router
from app.webhooks.mercadopago import router as mp_router
from app.agent.router import process_test_message
from app.tools.oauth_mp import complete_mp_oauth
from app.templates import (
    get_oauth_success_html,
    get_oauth_error_html,
    get_payment_success_html,
    get_payment_pending_html,
    get_payment_error_html,
    get_test_console_html,
)
from app.infra.sheets import get_order_by_id, get_all_vets
from pydantic import BaseModel

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager para la aplicación."""
    # Startup
    logger.info("Starting Direct to Vet Agent...")
    logger.info(f"Environment: {settings.env}")
    logger.info(f"Gemini Model: {settings.gemini_model}")
    logger.info(f"Twilio configured: {settings.has_twilio()}")
    logger.info(f"MP configured: {settings.has_mp()}")
    logger.info(f"SendGrid configured: {settings.has_sendgrid()}")

    yield

    # Shutdown
    logger.info("Shutting down Direct to Vet Agent...")


# Crear aplicación
app = FastAPI(
    title="Direct to Vet Agent",
    description="Agente conversacional para veterinarias - WhatsApp + Mercado Pago",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (para desarrollo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# ROUTERS
# =============================================================================

# Webhooks
app.include_router(twilio_router)
app.include_router(mp_router)


# =============================================================================
# ENDPOINTS PRINCIPALES
# =============================================================================


@app.get("/")
async def root():
    """Health check básico."""
    return {
        "service": "Direct to Vet Agent",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():
    """Health check detallado."""
    return {
        "status": "healthy",
        "environment": settings.env,
        "services": {
            "twilio": "configured" if settings.has_twilio() else "not_configured",
            "mercadopago": "configured" if settings.has_mp() else "not_configured",
            "sendgrid": "configured" if settings.has_sendgrid() else "not_configured",
        },
    }


# =============================================================================
# OAUTH MERCADO PAGO CALLBACK
# =============================================================================


@app.get("/mp/oauth/callback")
async def mp_oauth_callback(code: str = None, state: str = None, error: str = None):
    """
    Callback de OAuth de Mercado Pago.

    Cuando el veterinario autoriza la app, MP redirige aquí con:
    - code: código de autorización
    - state: contiene vet_id|timestamp

    O si hay error:
    - error: código de error
    """
    if error:
        logger.error(f"MP OAuth error: {error}")
        return HTMLResponse(
            content=get_oauth_error_html(f"Error de Mercado Pago: {error}"),
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            content=get_oauth_error_html("Faltan parámetros requeridos"),
            status_code=400,
        )

    # Parsear state para obtener vet_id
    try:
        parts = state.split("|")
        vet_id = parts[0]
    except Exception:
        return HTMLResponse(
            content=get_oauth_error_html("Parámetros inválidos"),
            status_code=400,
        )

    # Completar OAuth
    result = complete_mp_oauth(vet_id, code)

    if result["status"] == "success":
        # Página de éxito con branding
        return HTMLResponse(content=get_oauth_success_html())
    else:
        error_msg = result.get("message", "Error al conectar Mercado Pago")
        return HTMLResponse(
            content=get_oauth_error_html(error_msg),
            status_code=400,
        )


# =============================================================================
# PAYMENT RETURN PAGES (cuando el cliente vuelve del checkout de MP)
# =============================================================================


@app.get("/payment/success")
async def payment_success(
    order_id: str = None,
    collection_id: str = None,
    collection_status: str = None,
    payment_id: str = None,
    status: str = None,
    external_reference: str = None,
    preference_id: str = None,
):
    """
    Página de retorno cuando el pago fue exitoso.

    MP redirige aquí con parámetros del pago.
    """
    logger.info(f"Payment success return: order_id={order_id}, external_reference={external_reference}, status={status}")

    # Extraer order_id del external_reference si no viene en query params
    if not order_id and external_reference and "|" in external_reference:
        parts = external_reference.split("|")
        if len(parts) >= 3:
            order_id = parts[2]

    # Obtener monto del pedido
    amount = "Confirmado"
    if order_id:
        order = get_order_by_id(order_id)
        if order:
            amount = f"${order.total_amount:,.2f} {order.currency}"

    return HTMLResponse(
        content=get_payment_success_html(
            order_id=order_id or "N/A",
            amount=amount,
        )
    )


@app.get("/payment/pending")
async def payment_pending(
    order_id: str = None,
    collection_id: str = None,
    external_reference: str = None,
    preference_id: str = None,
):
    """
    Página de retorno cuando el pago quedó pendiente.
    """
    logger.info(f"Payment pending return: order_id={order_id}, external_reference={external_reference}")

    # Extraer order_id del external_reference si no viene en query params
    if not order_id and external_reference and "|" in external_reference:
        parts = external_reference.split("|")
        if len(parts) >= 3:
            order_id = parts[2]

    # Obtener monto del pedido
    amount = "Pendiente"
    if order_id:
        order = get_order_by_id(order_id)
        if order:
            amount = f"${order.total_amount:,.2f} {order.currency}"

    return HTMLResponse(
        content=get_payment_pending_html(
            order_id=order_id or "N/A",
            amount=amount,
        )
    )


@app.get("/payment/failure")
async def payment_failure(
    order_id: str = None,
    collection_id: str = None,
    external_reference: str = None,
    preference_id: str = None,
):
    """
    Página de retorno cuando el pago falló.
    """
    logger.info(f"Payment failure return: order_id={order_id}, external_reference={external_reference}")

    return HTMLResponse(
        content=get_payment_error_html(
            "El pago no pudo ser procesado. Verificá los datos de tu tarjeta o intentá con otro medio de pago."
        )
    )


# =============================================================================
# ENDPOINTS DE TESTING (solo en desarrollo)
# =============================================================================


@app.get("/test/pages/oauth-success")
async def test_oauth_success_page():
    """Preview de la página de OAuth exitoso."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_oauth_success_html())


@app.get("/test/pages/oauth-error")
async def test_oauth_error_page():
    """Preview de la página de OAuth error."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_oauth_error_html("Este es un error de prueba"))


@app.get("/test/pages/payment-success")
async def test_payment_success_page():
    """Preview de la página de pago exitoso."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_payment_success_html(order_id="ORD-TEST123", amount="$15,000.00 ARS"))


@app.get("/test/pages/payment-pending")
async def test_payment_pending_page():
    """Preview de la página de pago pendiente."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_payment_pending_html(order_id="ORD-TEST123", amount="$15,000.00 ARS"))


@app.get("/test/pages/payment-error")
async def test_payment_error_page():
    """Preview de la página de pago fallido."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_payment_error_html("El pago fue rechazado por fondos insuficientes"))


@app.post("/test/message")
async def test_message(request: Request):
    """
    Endpoint para probar el agente sin WhatsApp.

    Body:
    {
        "vet_id": "VET001",
        "message": "Hola, quiero buscar alimento para perro"
    }
    """
    if settings.is_production:
        return JSONResponse(
            status_code=403,
            content={"error": "Not available in production"},
        )

    body = await request.json()
    vet_id = body.get("vet_id")
    message = body.get("message")

    if not vet_id or not message:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing vet_id or message"},
        )

    result = await process_test_message(vet_id, message)
    return result


@app.get("/test/catalog")
async def test_catalog(query: str = "perro"):
    """
    Endpoint para probar búsqueda de catálogo.
    """
    if settings.is_production:
        return JSONResponse(
            status_code=403,
            content={"error": "Not available in production"},
        )

    from app.tools.catalog import search_catalog

    result = search_catalog("TEST_VET", query)
    return result


# =============================================================================
# CONSOLA DE TESTING (Dual Chat)
# =============================================================================


class TestMessageRequest(BaseModel):
    """Request body para mensajes de test."""
    phone: str
    message: str
    vet_id: str = None  # Solo para vet messages


@app.get("/test/console")
async def test_console():
    """
    Consola de testing con dos chats (VET y CLIENTE).
    """
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_test_console_html())


@app.get("/test/vets")
async def test_get_vets():
    """
    Lista de veterinarias registradas para el selector de la consola.
    """
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})

    vets = get_all_vets()
    return {
        "vets": [
            {
                "vet_id": v.vet_id,
                "name": v.name,
                "whatsapp_e164": v.whatsapp_e164,
                "mp_connected": v.mp_connected,
                "contact_name": v.contact_name,
            }
            for v in vets
        ]
    }


@app.post("/test/vet/message")
async def test_vet_message(request: Request):
    """
    Simula un mensaje de veterinario al agente.
    """
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})

    body = await request.json()
    vet_id = body.get("vet_id")
    phone = body.get("phone")
    message = body.get("message")

    if not vet_id or not message:
        return JSONResponse(status_code=400, content={"error": "Missing vet_id or message"})

    try:
        # Usar el router del agente directamente
        from app.agent.router import process_incoming_message

        response = await process_incoming_message(
            phone_e164=phone,
            message_text=message,
        )
        return {"response": response, "role": "vet"}
    except Exception as e:
        logger.error(f"Error in test vet message: {e}")
        return {"error": str(e)}


@app.post("/test/client/message")
async def test_client_message(request: Request):
    """
    Simula un mensaje de cliente al agente.

    El cliente se identifica por su teléfono (que NO debe estar en la lista de vets).
    """
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})

    body = await request.json()
    phone = body.get("phone", "+5491199999999")
    message = body.get("message")

    if not message:
        return JSONResponse(status_code=400, content={"error": "Missing message"})

    try:
        # Usar el router del agente directamente
        from app.agent.router import process_incoming_message

        response = await process_incoming_message(
            phone_e164=phone,
            message_text=message,
        )
        return {"response": response, "role": "client"}
    except Exception as e:
        logger.error(f"Error in test client message: {e}")
        return {"error": str(e)}


# =============================================================================
# ERROR HANDLERS
# =============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global de excepciones."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Error interno del servidor",
        },
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=not settings.is_production,
    )
