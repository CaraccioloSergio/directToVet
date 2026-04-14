"""
main.py
Aplicación FastAPI principal de Direct to Vet.
"""

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

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
    get_backoffice_console_html,
)
from app.infra.sheets import (
    get_order_by_id,
    get_all_vets,
    create_vet,
    update_vet,
    get_all_customers,
    create_customer,
    update_customer,
    get_catalog,
    upsert_product,
    get_all_orders,
    get_all_shipping_zones,
    get_shipping_cost,
    update_order_status as sheets_update_order_status,
    update_order_payment_status,
)
from pydantic import BaseModel
from typing import Optional, List

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


def _get_wa_number() -> str:
    """Extrae solo los dígitos del número de WhatsApp de Twilio para usar en wa.me links."""
    raw = settings.twilio_whatsapp_number or ""
    return raw.replace("whatsapp:", "").replace("+", "").replace(" ", "").strip()


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
        return HTMLResponse(content=get_oauth_success_html(whatsapp_number=_get_wa_number()))
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
            whatsapp_number=_get_wa_number(),
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
            whatsapp_number=_get_wa_number(),
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
    return HTMLResponse(content=get_oauth_success_html(whatsapp_number=_get_wa_number()))


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
    return HTMLResponse(content=get_payment_success_html(order_id="ORD-TEST123", amount="$15,000.00 ARS", whatsapp_number=_get_wa_number()))


@app.get("/test/pages/payment-pending")
async def test_payment_pending_page():
    """Preview de la página de pago pendiente."""
    if settings.is_production:
        return JSONResponse(status_code=403, content={"error": "Not available in production"})
    return HTMLResponse(content=get_payment_pending_html(order_id="ORD-TEST123", amount="$15,000.00 ARS", whatsapp_number=_get_wa_number()))


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
# BACKOFFICE (protegido con basic auth — disponible en producción)
# =============================================================================

_basic_security = HTTPBasic()


def _require_backoffice_auth(credentials: HTTPBasicCredentials = Depends(_basic_security)):
    """Dependencia de autenticación básica para el backoffice."""
    if not settings.backoffice_username or not settings.backoffice_password:
        raise HTTPException(status_code=503, detail="Backoffice not configured")

    valid_user = secrets.compare_digest(credentials.username, settings.backoffice_username)
    valid_pass = secrets.compare_digest(credentials.password, settings.backoffice_password)

    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

@app.get("/backoffice")
async def backoffice_console(username: str = Depends(_require_backoffice_auth)):
    return HTMLResponse(content=get_backoffice_console_html())


# --------------------------------------------------------------------------
# VETS
# --------------------------------------------------------------------------

@app.get("/backoffice/vets")
async def backoffice_get_vets(username: str = Depends(_require_backoffice_auth)):
    vets = get_all_vets()
    return {
        "vets": [
            {
                "vet_id": v.vet_id,
                "name": v.name,
                "whatsapp_e164": v.whatsapp_e164,
                "active": v.active,
                "mp_connected": v.mp_connected,
                "contact_name": v.contact_name or "",
                "address": v.address or "",
                "email": v.email or "",
                "distributor_id": v.distributor_id or "",
            }
            for v in vets
        ]
    }


class BackofficeCreateVetRequest(BaseModel):
    name: str
    whatsapp_e164: str
    contact_name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    distributor_id: Optional[str] = None


@app.post("/backoffice/vets")
async def backoffice_create_vet(
    body: BackofficeCreateVetRequest,
    username: str = Depends(_require_backoffice_auth),
):
    vet = create_vet(
        name=body.name,
        whatsapp_e164=body.whatsapp_e164,
        contact_name=body.contact_name,
        address=body.address,
        email=body.email,
        distributor_id=body.distributor_id,
    )
    if vet is None:
        return JSONResponse(status_code=500, content={"error": "Error al crear la veterinaria"})
    return {"status": "created", "vet": vet.model_dump()}


class BackofficeUpdateVetRequest(BaseModel):
    name: Optional[str] = None
    whatsapp_e164: Optional[str] = None
    active: Optional[bool] = None
    contact_name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    distributor_id: Optional[str] = None


@app.patch("/backoffice/vets/{vet_id}")
async def backoffice_update_vet(
    vet_id: str,
    body: BackofficeUpdateVetRequest,
    username: str = Depends(_require_backoffice_auth),
):
    ok = update_vet(vet_id=vet_id, **body.model_dump(exclude_none=True))
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Veterinaria no encontrada"})
    return {"status": "updated"}


# --------------------------------------------------------------------------
# CLIENTES
# --------------------------------------------------------------------------

@app.get("/backoffice/customers")
async def backoffice_get_customers(
    vet_id: Optional[str] = None,
    search: Optional[str] = None,
    username: str = Depends(_require_backoffice_auth),
):
    customers = get_all_customers(vet_id=vet_id, search=search)
    return {
        "customers": [
            {
                "customer_id": c.customer_id,
                "vet_id": c.vet_id,
                "name": c.name,
                "lastname": c.lastname,
                "email": c.email,
                "whatsapp_e164": c.whatsapp_e164,
                "address": c.address or "",
                "pet_type": c.pet_type or "",
                "pet_name": c.pet_name or "",
                "notes": c.notes or "",
            }
            for c in customers
        ]
    }


class BackofficeCreateCustomerRequest(BaseModel):
    vet_id: str
    name: str
    lastname: str
    email: str
    whatsapp_e164: str
    address: Optional[str] = None
    pet_type: Optional[str] = None
    pet_name: Optional[str] = None
    notes: Optional[str] = None


@app.post("/backoffice/customers")
async def backoffice_create_customer(
    body: BackofficeCreateCustomerRequest,
    username: str = Depends(_require_backoffice_auth),
):
    customer = create_customer(
        vet_id=body.vet_id,
        name=body.name,
        lastname=body.lastname,
        email=body.email,
        whatsapp_e164=body.whatsapp_e164,
        address=body.address,
        pet_type=body.pet_type,
        pet_name=body.pet_name,
        notes=body.notes,
    )
    if customer is None:
        return JSONResponse(status_code=500, content={"error": "Error al crear el cliente"})
    return {"status": "created", "customer_id": customer.customer_id}


class BackofficeUpdateCustomerRequest(BaseModel):
    address: Optional[str] = None
    email: Optional[str] = None
    whatsapp_e164: Optional[str] = None
    pet_type: Optional[str] = None
    pet_name: Optional[str] = None
    notes: Optional[str] = None


@app.patch("/backoffice/customers/{customer_id}")
async def backoffice_update_customer(
    customer_id: str,
    body: BackofficeUpdateCustomerRequest,
    username: str = Depends(_require_backoffice_auth),
):
    ok = update_customer(customer_id=customer_id, **body.model_dump(exclude_none=True))
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Cliente no encontrado"})
    return {"status": "updated"}


# --------------------------------------------------------------------------
# CATÁLOGO
# --------------------------------------------------------------------------

@app.get("/backoffice/catalog")
async def backoffice_get_catalog(
    search: Optional[str] = None,
    active_only: bool = False,
    username: str = Depends(_require_backoffice_auth),
):
    from app.infra.sheets import search_products
    products = search_products(search, vet_id=None) if search else get_catalog(active_only=active_only)
    return {
        "products": [
            {
                "sku": p.sku,
                "ean": p.ean or "",
                "product_name": p.product_name,
                "presentation": p.presentation or "",
                "description": p.description or "",
                "price_distributor": float(p.price_distributor),
                "price_customer": float(p.price_customer),
                "currency": p.currency,
                "stock": p.stock,
                "active": p.active,
            }
            for p in products
        ]
    }


class BackofficeUpsertProductRequest(BaseModel):
    sku: str
    product_name: str
    price_customer: float
    price_distributor: float
    stock: int
    ean: Optional[str] = None
    presentation: Optional[str] = None
    description: Optional[str] = None
    currency: str = "ARS"
    active: bool = True


@app.post("/backoffice/catalog/product")
async def backoffice_upsert_product(
    body: BackofficeUpsertProductRequest,
    username: str = Depends(_require_backoffice_auth),
):
    from decimal import Decimal
    result = upsert_product(
        sku=body.sku,
        product_name=body.product_name,
        price_customer=Decimal(str(body.price_customer)),
        price_distributor=Decimal(str(body.price_distributor)),
        stock=body.stock,
        ean=body.ean,
        presentation=body.presentation,
        description=body.description,
        currency=body.currency,
        active=body.active,
    )
    if result.get("action") == "error":
        return JSONResponse(status_code=500, content={"error": result.get("error")})
    return result


@app.post("/backoffice/catalog/csv")
async def backoffice_catalog_csv(
    request: Request,
    username: str = Depends(_require_backoffice_auth),
):
    """
    Importa/actualiza productos desde un CSV.

    Columnas requeridas: sku, product_name, price_customer, price_distributor, stock
    Columnas opcionales: ean, presentation, description, currency, active
    """
    from fastapi import UploadFile, File
    from decimal import Decimal
    import csv
    import io

    try:
        form = await request.form()
        file = form.get("file")
        if file is None:
            return JSONResponse(status_code=400, content={"error": "No file uploaded"})

        content = await file.read()
        text = content.decode("utf-8-sig")  # -sig handles BOM from Excel

        reader = csv.DictReader(io.StringIO(text))
        results = {"created": 0, "updated": 0, "errors": []}

        for row in reader:
            try:
                sku = row.get("sku", "").strip()
                if not sku:
                    continue

                result = upsert_product(
                    sku=sku,
                    product_name=row.get("product_name", "").strip(),
                    price_customer=Decimal(str(row.get("price_customer", 0))),
                    price_distributor=Decimal(str(row.get("price_distributor", 0))),
                    stock=int(row.get("stock", 0)),
                    ean=row.get("ean", "").strip() or None,
                    presentation=row.get("presentation", "").strip() or None,
                    description=row.get("description", "").strip() or None,
                    currency=row.get("currency", "ARS").strip() or "ARS",
                    active=str(row.get("active", "true")).lower() not in ("false", "0", "no"),
                )

                if result.get("action") == "created":
                    results["created"] += 1
                elif result.get("action") == "updated":
                    results["updated"] += 1
                else:
                    results["errors"].append(f"SKU {sku}: {result.get('error')}")

            except Exception as e:
                results["errors"].append(f"Row error: {str(e)}")

        return results

    except Exception as e:
        logger.error(f"CSV import error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# --------------------------------------------------------------------------
# ÓRDENES
# --------------------------------------------------------------------------

@app.get("/backoffice/orders")
async def backoffice_get_orders(
    vet_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    username: str = Depends(_require_backoffice_auth),
):
    orders = get_all_orders(vet_id=vet_id, status=status, search=search, limit=limit)
    return {
        "orders": [
            {
                "order_id": o.order_id,
                "vet_id": o.vet_id,
                "customer_name": o.customer.full_name,
                "customer_email": o.customer.email,
                "customer_phone": o.customer.whatsapp_e164,
                "delivery_mode": o.delivery.mode.value,
                "delivery_zone": o.delivery.zone or "",
                "items_count": sum(i.quantity for i in o.items),
                "subtotal": float(o.subtotal),
                "shipping_cost": float(o.shipping_cost),
                "total_amount": float(o.total_amount),
                "currency": o.currency,
                "status": o.status.value,
                "payment_method": o.payment_method.value if o.payment_method else "",
                "mp_preference_id": o.mp_preference_id or "",
                "mp_payment_id": o.mp_payment_id or "",
                "mp_status": o.mp_status.value if o.mp_status else "",
                "created_at": o.created_at.isoformat(),
            }
            for o in orders
        ]
    }


class BackofficeOrderItem(BaseModel):
    sku: str
    product_name: str
    quantity: int
    unit_price: float


class BackofficeCreateOrderRequest(BaseModel):
    vet_id: str
    customer_name: str
    customer_lastname: str
    customer_email: str
    customer_phone: str
    delivery_mode: str = "PICKUP"
    delivery_address: Optional[str] = None
    delivery_zone: Optional[str] = None
    items: List[BackofficeOrderItem]
    payment_method: str = "AT_VET"


@app.post("/backoffice/orders/create")
async def backoffice_create_order(
    body: BackofficeCreateOrderRequest,
    username: str = Depends(_require_backoffice_auth),
):
    """
    Crea un pedido directamente desde el backoffice.
    No usa el carrito del agente. No dispara notificaciones WhatsApp.
    """
    from decimal import Decimal
    from app.models.schemas import (
        Order, OrderStatus, CustomerData, DeliveryData,
        DeliveryMode, PaymentMethod, CartItem,
    )
    import uuid

    try:
        # Validar cliente
        customer = CustomerData(
            name=body.customer_name.strip(),
            lastname=body.customer_lastname.strip(),
            email=body.customer_email.strip().lower(),
            whatsapp_e164=body.customer_phone,
        )

        # Validar delivery
        mode = DeliveryMode(body.delivery_mode.upper())
        delivery = DeliveryData(
            mode=mode,
            address=body.delivery_address,
            zone=body.delivery_zone,
        )

        # Validar items
        if not body.items:
            return JSONResponse(status_code=400, content={"error": "El pedido debe tener al menos un producto"})

        cart_items = [
            CartItem(
                product_sku=item.sku,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=Decimal(str(item.unit_price)),
            )
            for item in body.items
        ]

        subtotal = sum(i.subtotal for i in cart_items)

        # Calcular envío
        shipping_cost = Decimal("0")
        if mode == DeliveryMode.DELIVERY and body.delivery_zone:
            cost = get_shipping_cost(body.delivery_zone)
            if cost is not None:
                shipping_cost = cost

        total = subtotal + shipping_cost

        # Método de pago y estado inicial
        try:
            pm = PaymentMethod(body.payment_method.upper())
        except ValueError:
            pm = PaymentMethod.AT_VET

        if pm == PaymentMethod.MERCADOPAGO:
            initial_status = OrderStatus.PAYMENT_PENDING_MP
        else:
            initial_status = OrderStatus.PAYMENT_AT_VET

        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        order = Order(
            order_id=order_id,
            vet_id=body.vet_id,
            customer=customer,
            delivery=delivery,
            items=cart_items,
            subtotal=subtotal,
            shipping_cost=shipping_cost,
            total_amount=total,
            status=initial_status,
            payment_method=pm,
        )

        from app.infra.sheets import create_order_record
        ok = create_order_record(order)

        if not ok:
            return JSONResponse(status_code=500, content={"error": "Error al guardar el pedido"})

        # Registrar cliente si no existe
        create_customer(
            vet_id=body.vet_id,
            name=body.customer_name,
            lastname=body.customer_lastname,
            email=body.customer_email,
            whatsapp_e164=body.customer_phone,
        )

        return {
            "status": "created",
            "order_id": order_id,
            "total_amount": float(total),
            "order_status": initial_status.value,
        }

    except Exception as e:
        logger.error(f"Backoffice create order error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# --------------------------------------------------------------------------
# PAGOS — MP STATUS
# --------------------------------------------------------------------------

@app.get("/backoffice/orders/{order_id}/mp-status")
async def backoffice_mp_status(
    order_id: str,
    username: str = Depends(_require_backoffice_auth),
):
    """
    Consulta el estado de pago en la API de Mercado Pago para un pedido.
    Devuelve el estado actual de MP y si difiere del pedido, permite sincronizar.
    """
    from app.tools.oauth_mp import ensure_valid_mp_token
    from app.tools.payments import _get_mp_payment

    order = get_order_by_id(order_id)
    if order is None:
        return JSONResponse(status_code=404, content={"error": "Pedido no encontrado"})

    result = {
        "order_id": order_id,
        "order_status": order.status.value,
        "payment_method": order.payment_method.value if order.payment_method else "",
        "mp_payment_id": order.mp_payment_id or "",
        "mp_status": order.mp_status.value if order.mp_status else "",
        "total_amount": float(order.total_amount),
        "can_sync": False,
        "mp_live_status": None,
        "mp_live_detail": None,
        "recommended_status": None,
    }

    if not order.mp_payment_id:
        result["note"] = "Sin payment_id de MP registrado aún (el cliente no completó el pago)"
        return result

    # Obtener token de la vet
    token_res = ensure_valid_mp_token(order.vet_id)
    if token_res["status"] != "success":
        result["note"] = "No se pudo obtener el token de MP de la vet"
        return result

    # Consultar estado real en MP
    payment = _get_mp_payment(token_res["access_token"], order.mp_payment_id)
    if payment is None:
        result["note"] = "No se pudo obtener el pago de la API de MP"
        return result

    mp_status = payment.get("status", "")
    mp_detail = payment.get("status_detail", "")
    result["mp_live_status"] = mp_status
    result["mp_live_detail"] = mp_detail

    # Determinar si hay que sincronizar
    from app.models.schemas import OrderStatus, MPPaymentStatus
    STATUS_MAP = {
        "approved": OrderStatus.PAYMENT_APPROVED,
        "rejected": OrderStatus.PAYMENT_REJECTED,
        "cancelled": OrderStatus.CANCELLED,
    }
    recommended = STATUS_MAP.get(mp_status)
    if recommended and order.status != recommended:
        result["can_sync"] = True
        result["recommended_status"] = recommended.value

    return result


@app.post("/backoffice/orders/{order_id}/sync-payment")
async def backoffice_sync_payment(
    order_id: str,
    username: str = Depends(_require_backoffice_auth),
):
    """
    Sincroniza el estado del pedido con el estado real de MP.
    Llama a la API de MP, obtiene el estado y actualiza el pedido.
    """
    from app.tools.oauth_mp import ensure_valid_mp_token
    from app.tools.payments import _get_mp_payment
    from app.models.schemas import OrderStatus, MPPaymentStatus

    order = get_order_by_id(order_id)
    if order is None:
        return JSONResponse(status_code=404, content={"error": "Pedido no encontrado"})

    if not order.mp_payment_id:
        return JSONResponse(status_code=400, content={"error": "El pedido no tiene payment_id de MP"})

    token_res = ensure_valid_mp_token(order.vet_id)
    if token_res["status"] != "success":
        return JSONResponse(status_code=503, content={"error": "No se pudo obtener token de MP"})

    payment = _get_mp_payment(token_res["access_token"], order.mp_payment_id)
    if payment is None:
        return JSONResponse(status_code=503, content={"error": "No se pudo consultar MP"})

    mp_status_str = payment.get("status", "")

    STATUS_MAP = {
        "approved": (OrderStatus.PAYMENT_APPROVED, MPPaymentStatus.APPROVED),
        "rejected": (OrderStatus.PAYMENT_REJECTED, MPPaymentStatus.REJECTED),
        "cancelled": (OrderStatus.CANCELLED, MPPaymentStatus.CANCELLED),
        "in_process": (None, MPPaymentStatus.IN_PROCESS),
        "pending": (None, MPPaymentStatus.PENDING),
    }
    mapping = STATUS_MAP.get(mp_status_str)

    if not mapping:
        return {"status": "no_change", "mp_status": mp_status_str}

    new_order_status, new_mp_status = mapping

    ok = update_order_payment_status(
        order_id=order_id,
        mp_payment_id=order.mp_payment_id,
        mp_status=new_mp_status,
        status=new_order_status or order.status,
    )

    return {
        "status": "synced" if ok else "error",
        "mp_status": mp_status_str,
        "order_status": (new_order_status or order.status).value,
    }


# --------------------------------------------------------------------------
# MANUAL STATUS UPDATE
# --------------------------------------------------------------------------

class BackofficeStatusUpdateRequest(BaseModel):
    new_status: str

@app.patch("/backoffice/orders/{order_id}/status")
async def backoffice_update_order_status(
    order_id: str,
    body: BackofficeStatusUpdateRequest,
    username: str = Depends(_require_backoffice_auth),
):
    """
    Actualiza el estado de un pedido manualmente desde el backoffice.
    No dispara notificaciones. Valida que el estado sea un valor válido de OrderStatus.
    """
    from app.models.schemas import OrderStatus

    try:
        new_status = OrderStatus(body.new_status)
    except ValueError:
        valid = [s.value for s in OrderStatus]
        return JSONResponse(status_code=400, content={"error": f"Estado inválido. Valores válidos: {valid}"})

    order = get_order_by_id(order_id)
    if order is None:
        return JSONResponse(status_code=404, content={"error": "Pedido no encontrado"})

    ok = sheets_update_order_status(order_id, new_status)
    if not ok:
        return JSONResponse(status_code=500, content={"error": "Error al actualizar el estado en Sheets"})

    return {"status": "updated", "order_id": order_id, "new_status": new_status.value}


# --------------------------------------------------------------------------
# CHAT (agente — desde backoffice)
# --------------------------------------------------------------------------

@app.post("/backoffice/vet/message")
async def backoffice_vet_message(
    request: Request,
    username: str = Depends(_require_backoffice_auth),
):
    body = await request.json()
    vet_id = body.get("vet_id")
    phone = body.get("phone")
    message = body.get("message")

    if not vet_id or not message:
        return JSONResponse(status_code=400, content={"error": "Missing vet_id or message"})

    try:
        from app.agent.router import process_incoming_message
        response = await process_incoming_message(phone_e164=phone, message_text=message)
        return {"response": response, "role": "vet"}
    except Exception as e:
        logger.error(f"Backoffice vet message error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/backoffice/client/message")
async def backoffice_client_message(
    request: Request,
    username: str = Depends(_require_backoffice_auth),
):
    body = await request.json()
    phone = body.get("phone", "+5491199999999")
    message = body.get("message")

    if not message:
        return JSONResponse(status_code=400, content={"error": "Missing message"})

    try:
        from app.agent.router import process_incoming_message
        response = await process_incoming_message(phone_e164=phone, message_text=message)
        return {"response": response, "role": "client"}
    except Exception as e:
        logger.error(f"Backoffice client message error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# --------------------------------------------------------------------------
# MISC — shipping / payment link / OAuth
# --------------------------------------------------------------------------

@app.get("/backoffice/shipping-zones")
async def backoffice_shipping_zones(username: str = Depends(_require_backoffice_auth)):
    zones = get_all_shipping_zones()
    return {"zones": [{"zone": z["zone"], "price": float(z["price"])} for z in zones]}


@app.get("/backoffice/shipping-cost")
async def backoffice_shipping_cost(
    zone: str,
    username: str = Depends(_require_backoffice_auth),
):
    cost = get_shipping_cost(zone)
    if cost is None:
        return JSONResponse(status_code=404, content={"error": f"Zona '{zone}' no encontrada"})
    return {"zone": zone, "cost": float(cost)}


class BackofficePaymentLinkRequest(BaseModel):
    vet_id: str
    order_id: str


@app.post("/backoffice/payment-link")
async def backoffice_payment_link(
    body: BackofficePaymentLinkRequest,
    username: str = Depends(_require_backoffice_auth),
):
    from app.tools.payments import create_payment_link
    return create_payment_link(vet_id=body.vet_id, order_id=body.order_id)


@app.post("/backoffice/oauth-link/{vet_id}")
async def backoffice_oauth_link(
    vet_id: str,
    username: str = Depends(_require_backoffice_auth),
):
    from app.tools.oauth_mp import start_mp_oauth
    return start_mp_oauth(vet_id=vet_id)


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
