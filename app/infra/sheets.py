"""
sheets.py
Capa de acceso a Google Sheets.
Maneja lectura/escritura de vets, catalog, orders y events.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional
from functools import lru_cache

from dateutil import parser as dateutil_parser

import gspread


def _parse_datetime(value: str) -> datetime:
    """Parsea cualquier formato de fecha que devuelva Google Sheets."""
    if not value:
        return datetime.now()
    try:
        return dateutil_parser.parse(str(value))
    except Exception:
        return datetime.now()
from google.oauth2.service_account import Credentials

from app.config import get_settings
from app.models.schemas import (
    VetContext,
    Product,
    Order,
    OrderStatus,
    Event,
    EventType,
    CartItem,
    Customer,
    CustomerData,
    DeliveryData,
    DeliveryMode,
    PaymentMethod,
    MPPaymentStatus,
)

logger = logging.getLogger(__name__)


# ===========================================
# HELPERS
# ===========================================

def normalize_phone(phone: str) -> str:
    """
    Normaliza un número de teléfono al formato E.164.

    Maneja casos comunes de Google Sheets:
    - El + fue reemplazado por = (fórmula)
    - El número no tiene + al inicio
    - Espacios o guiones extras
    """
    if not phone:
        return ""

    # Convertir a string y limpiar
    phone = str(phone).strip()

    # Si empieza con = (Google Sheets lo interpretó como fórmula)
    if phone.startswith("="):
        phone = "+" + phone[1:]

    # Remover espacios y guiones
    phone = phone.replace(" ", "").replace("-", "")

    # Si es solo números sin +, agregar +
    if phone and phone[0].isdigit():
        phone = "+" + phone

    return phone


# ===========================================
# CONEXIÓN
# ===========================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


@lru_cache
def get_sheets_client() -> gspread.Client:
    """
    Obtiene cliente de Google Sheets (singleton).

    Soporta dos modos de autenticación:
    1. JSON en variable de entorno (para containers/AppRunner)
    2. Archivo de credenciales (para desarrollo local)
    """
    settings = get_settings()

    # Prioridad: JSON en env var > archivo local
    if settings.google_sheets_credentials_json:
        # Credentials from JSON string (for container deployments)
        creds_dict = json.loads(settings.google_sheets_credentials_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        logger.info("Using Google Sheets credentials from environment variable")
    else:
        # Credentials from file (for local development)
        creds = Credentials.from_service_account_file(
            settings.google_sheets_credentials_path,
            scopes=SCOPES,
        )
        logger.info("Using Google Sheets credentials from file")

    return gspread.authorize(creds)


def get_spreadsheet() -> gspread.Spreadsheet:
    """Obtiene el spreadsheet principal."""
    settings = get_settings()
    client = get_sheets_client()
    return client.open_by_key(settings.google_sheets_spreadsheet_id)


def get_worksheet(name: str) -> gspread.Worksheet:
    """Obtiene una hoja por nombre."""
    spreadsheet = get_spreadsheet()
    try:
        return spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        # Listar hojas disponibles para debug
        available = [ws.title for ws in spreadsheet.worksheets()]
        logger.error(f"Worksheet '{name}' not found. Available sheets: {available}")
        raise


# ===========================================
# VETS
# ===========================================

def get_all_vets() -> list[VetContext]:
    """Obtiene todas las veterinarias."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_vets)
        records = ws.get_all_records()

        vets = []
        for row in records:
            try:
                vet = VetContext(
                    vet_id=str(row.get("vet_id", "")),
                    name=str(row.get("name", "")),
                    whatsapp_e164=normalize_phone(row.get("whatsapp_e164", "")),
                    active=_parse_bool(row.get("active", False)),
                    mp_connected=_parse_bool(row.get("mp_connected", False)),
                    mp_user_id=str(row.get("mp_user_id", "")) or None,
                    # Campos adicionales
                    contact_name=str(row.get("contact_name", "")) or None,
                    address=str(row.get("address", "")) or None,
                    email=str(row.get("email", "")) or None,
                    distributor_id=str(row.get("distributor_id", "")) or None,
                )
                vets.append(vet)
            except Exception as e:
                logger.warning(f"Error parsing vet row: {row}, error: {e}")
                continue

        return vets
    except Exception as e:
        logger.error(f"Error reading vets sheet: {e}")
        return []


def get_vet_by_phone(phone_e164: str) -> Optional[VetContext]:
    """Busca veterinaria por teléfono."""
    phone_normalized = normalize_phone(phone_e164)
    vets = get_all_vets()
    for vet in vets:
        if vet.whatsapp_e164 == phone_normalized and vet.active:
            return vet
    return None


def get_vet_by_id(vet_id: str) -> Optional[VetContext]:
    """Busca veterinaria por ID."""
    vets = get_all_vets()
    for vet in vets:
        if vet.vet_id == vet_id:
            return vet
    return None


def update_vet_mp_status(vet_id: str, mp_connected: bool, mp_user_id: Optional[str] = None) -> bool:
    """Actualiza el estado de conexión de MP de una vet."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_vets)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):  # +2 por header y 0-index
            if str(row.get("vet_id", "")) == vet_id:
                # Encontrar columnas
                headers = ws.row_values(1)
                col_mp_connected = headers.index("mp_connected") + 1
                col_mp_user_id = headers.index("mp_user_id") + 1
                col_updated_at = headers.index("updated_at") + 1

                # Actualizar celdas
                ws.update_cell(i, col_mp_connected, mp_connected)
                if mp_user_id:
                    ws.update_cell(i, col_mp_user_id, mp_user_id)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Updated MP status for vet {vet_id}: connected={mp_connected}")
                return True

        logger.warning(f"Vet {vet_id} not found for MP status update")
        return False
    except Exception as e:
        logger.error(f"Error updating vet MP status: {e}")
        return False


# ===========================================
# CUSTOMERS
# ===========================================

def get_customers_by_vet(vet_id: str) -> list[Customer]:
    """Obtiene todos los clientes de una veterinaria."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_customers)
        records = ws.get_all_records()

        customers = []
        for row in records:
            try:
                if str(row.get("vet_id", "")) != vet_id:
                    continue
                if not row.get("active", True):
                    continue

                customer = Customer(
                    customer_id=str(row.get("customer_id", "")),
                    vet_id=str(row.get("vet_id", "")),
                    name=str(row.get("name", "")),
                    lastname=str(row.get("lastname", "")),
                    email=str(row.get("email", "")),
                    whatsapp_e164=normalize_phone(row.get("whatsapp_e164", "")),
                    address=str(row.get("address", "")) or None,
                    pet_type=str(row.get("pet_type", "")) or None,
                    pet_name=str(row.get("pet_name", "")) or None,
                    notes=str(row.get("notes", "")) or None,
                    active=bool(row.get("active", True)),
                )
                customers.append(customer)
            except Exception as e:
                logger.warning(f"Error parsing customer row: {row}, error: {e}")
                continue

        return customers
    except Exception as e:
        logger.error(f"Error reading customers sheet: {e}")
        return []


def search_customers(
    vet_id: str,
    query: str = "",
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> list[Customer]:
    """
    Busca clientes por nombre, teléfono o email.
    Si se provee phone o email exacto, filtra por esos.
    Si se provee query, busca en nombre/apellido.
    """
    customers = get_customers_by_vet(vet_id)

    if phone:
        # Buscar por teléfono (normalizado)
        phone_normalized = normalize_phone(phone)
        return [c for c in customers if phone_normalized in c.whatsapp_e164]

    if email:
        # Buscar por email exacto
        return [c for c in customers if c.email.lower() == email.lower()]

    if query:
        # Buscar en nombre y apellido
        query_lower = query.lower().strip()
        return [
            c for c in customers
            if query_lower in c.name.lower()
            or query_lower in c.lastname.lower()
            or query_lower in c.full_name.lower()
        ]

    return customers


def get_customer_by_id(customer_id: str) -> Optional[Customer]:
    """Obtiene un cliente por ID."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_customers)
        records = ws.get_all_records()

        for row in records:
            if str(row.get("customer_id", "")) == customer_id:
                return Customer(
                    customer_id=str(row.get("customer_id", "")),
                    vet_id=str(row.get("vet_id", "")),
                    name=str(row.get("name", "")),
                    lastname=str(row.get("lastname", "")),
                    email=str(row.get("email", "")),
                    whatsapp_e164=normalize_phone(row.get("whatsapp_e164", "")),
                    address=str(row.get("address", "")) or None,
                    pet_type=str(row.get("pet_type", "")) or None,
                    pet_name=str(row.get("pet_name", "")) or None,
                    notes=str(row.get("notes", "")) or None,
                    active=bool(row.get("active", True)),
                )
        return None
    except Exception as e:
        logger.error(f"Error getting customer {customer_id}: {e}")
        return None


def get_customer_by_phone_or_email(
    vet_id: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Customer]:
    """
    Busca un cliente por teléfono o email dentro de una veterinaria.
    Útil para evitar duplicados al crear clientes.
    """
    if not phone and not email:
        return None

    customers = get_customers_by_vet(vet_id)

    phone_normalized = normalize_phone(phone) if phone else None

    for customer in customers:
        if phone_normalized and customer.whatsapp_e164 == phone_normalized:
            return customer
        if email and customer.email.lower() == email.lower():
            return customer

    return None


def get_customer_by_phone_global(phone: str) -> Optional[Customer]:
    """
    Busca un cliente por teléfono en TODAS las veterinarias.

    Usado para identificar si un número de WhatsApp pertenece a un cliente.

    Args:
        phone: Número de teléfono a buscar

    Returns:
        Customer si existe, None si no
    """
    settings = get_settings()
    try:
        phone_normalized = normalize_phone(phone)
        if not phone_normalized:
            return None

        ws = get_worksheet(settings.sheet_customers)
        records = ws.get_all_records()

        for row in records:
            try:
                if not row.get("active", True):
                    continue

                row_phone = normalize_phone(row.get("whatsapp_e164", ""))
                if row_phone == phone_normalized:
                    return Customer(
                        customer_id=str(row.get("customer_id", "")),
                        vet_id=str(row.get("vet_id", "")),
                        name=str(row.get("name", "")),
                        lastname=str(row.get("lastname", "")),
                        email=str(row.get("email", "")),
                        whatsapp_e164=row_phone,
                        address=str(row.get("address", "")) or None,
                        pet_type=str(row.get("pet_type", "")) or None,
                        pet_name=str(row.get("pet_name", "")) or None,
                        notes=str(row.get("notes", "")) or None,
                        active=True,
                    )
            except Exception as e:
                logger.warning(f"Error parsing customer row: {e}")
                continue

        return None
    except Exception as e:
        logger.error(f"Error searching customer by phone: {e}")
        return None


def create_customer(
    vet_id: str,
    name: str,
    lastname: str,
    email: str,
    whatsapp_e164: str,
    address: Optional[str] = None,
    pet_type: Optional[str] = None,
    pet_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Customer]:
    """
    Crea un nuevo cliente en el sheet.

    Primero verifica si ya existe un cliente con el mismo teléfono o email
    para evitar duplicados. Si existe, retorna el existente.

    Returns:
        Customer creado o existente, None si hay error.
    """
    settings = get_settings()
    try:
        # Verificar si ya existe
        existing = get_customer_by_phone_or_email(
            vet_id=vet_id,
            phone=whatsapp_e164,
            email=email,
        )
        if existing:
            logger.info(f"Customer already exists: {existing.customer_id}")
            return existing

        # Generar ID único
        import uuid
        customer_id = f"CUST-{uuid.uuid4().hex[:8].upper()}"

        # Normalizar teléfono
        phone_normalized = normalize_phone(whatsapp_e164)

        ws = get_worksheet(settings.sheet_customers)

        now = datetime.utcnow().isoformat()
        row = [
            customer_id,
            vet_id,
            name.strip(),
            lastname.strip(),
            email.strip().lower(),
            phone_normalized,
            address or "",
            pet_type or "",
            pet_name or "",
            notes or "",
            True,  # active
            now,   # created_at
            now,   # updated_at
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Created customer: {customer_id} for vet {vet_id}")

        return Customer(
            customer_id=customer_id,
            vet_id=vet_id,
            name=name.strip(),
            lastname=lastname.strip(),
            email=email.strip().lower(),
            whatsapp_e164=phone_normalized,
            address=address,
            pet_type=pet_type,
            pet_name=pet_name,
            notes=notes,
            active=True,
        )

    except Exception as e:
        logger.error(f"Error creating customer: {e}")
        return None


def update_customer(
    customer_id: str,
    address: Optional[str] = None,
    email: Optional[str] = None,
    whatsapp_e164: Optional[str] = None,
    pet_type: Optional[str] = None,
    pet_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    """
    Actualiza datos de un cliente existente.

    Solo actualiza los campos que se pasan (no None).

    Args:
        customer_id: ID del cliente a actualizar
        address: Nueva dirección (opcional)
        email: Nuevo email (opcional)
        whatsapp_e164: Nuevo teléfono (opcional)
        pet_type: Tipo de mascota (opcional)
        pet_name: Nombre de mascota (opcional)
        notes: Nuevas notas (opcional)

    Returns:
        True si se actualizó correctamente
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_customers)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("customer_id", "")) == customer_id:
                headers = ws.row_values(1)

                # Actualizar solo los campos proporcionados
                if address is not None and "address" in headers:
                    col = headers.index("address") + 1
                    ws.update_cell(i, col, address)

                if email is not None and "email" in headers:
                    col = headers.index("email") + 1
                    ws.update_cell(i, col, email.strip().lower())

                if whatsapp_e164 is not None and "whatsapp_e164" in headers:
                    col = headers.index("whatsapp_e164") + 1
                    ws.update_cell(i, col, normalize_phone(whatsapp_e164))

                if pet_type is not None and "pet_type" in headers:
                    col = headers.index("pet_type") + 1
                    ws.update_cell(i, col, pet_type)

                if pet_name is not None and "pet_name" in headers:
                    col = headers.index("pet_name") + 1
                    ws.update_cell(i, col, pet_name)

                if notes is not None and "notes" in headers:
                    col = headers.index("notes") + 1
                    ws.update_cell(i, col, notes)

                # Actualizar timestamp
                if "updated_at" in headers:
                    col = headers.index("updated_at") + 1
                    ws.update_cell(i, col, datetime.utcnow().isoformat())

                logger.info(f"Updated customer {customer_id}")
                return True

        logger.warning(f"Customer not found for update: {customer_id}")
        return False
    except Exception as e:
        logger.error(f"Error updating customer: {e}")
        return False


def get_orders_by_customer(
    vet_id: str,
    customer_phone: Optional[str] = None,
    customer_email: Optional[str] = None,
    customer_name: Optional[str] = None,
    status: Optional[OrderStatus] = None,
) -> list[Order]:
    """
    Busca pedidos de un cliente específico.
    Puede filtrar por teléfono, email o nombre.
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        orders = []
        for row in records:
            try:
                # Filtrar por vet_id
                if str(row.get("vet_id", "")) != vet_id:
                    continue

                # Filtrar por criterios de búsqueda
                match = False

                if customer_phone:
                    phone_normalized = normalize_phone(customer_phone)
                    row_phone = normalize_phone(row.get("customer_whatsapp_e164", ""))
                    if phone_normalized in row_phone:
                        match = True

                if customer_email:
                    if str(row.get("customer_email", "")).lower() == customer_email.lower():
                        match = True

                if customer_name:
                    name_lower = customer_name.lower()
                    full_name = f"{row.get('customer_name', '')} {row.get('customer_lastname', '')}".lower()
                    if name_lower in full_name:
                        match = True

                if not (customer_phone or customer_email or customer_name):
                    # Si no hay criterio, retorna todos del vet
                    match = True

                if not match:
                    continue

                # Filtrar por status si se especifica
                if status and str(row.get("status", "")) != status.value:
                    continue

                order = _parse_order_row(row)
                orders.append(order)
            except Exception as e:
                logger.warning(f"Error parsing order row: {row}, error: {e}")
                continue

        # Ordenar por fecha de creación descendente
        orders.sort(key=lambda o: o.created_at, reverse=True)
        return orders
    except Exception as e:
        logger.error(f"Error searching orders: {e}")
        return []


# ===========================================
# CATALOG
# ===========================================

def get_catalog(vet_id: Optional[str] = None, active_only: bool = True) -> list[Product]:
    """
    Obtiene el catálogo de productos.
    Por ahora el catálogo es compartido (vet_id se ignora).
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_catalog)
        records = ws.get_all_records()

        products = []
        for row in records:
            try:
                # Filtrar inactivos si corresponde
                if active_only and not row.get("active", False):
                    continue

                product = Product(
                    sku=str(row.get("sku", "")),
                    ean=str(row.get("ean", "")) or None,
                    product_name=str(row.get("product_name", "")),
                    presentation=str(row.get("presentation", "")) or None,
                    description=str(row.get("description", "")) or None,
                    price_distributor=Decimal(str(row.get("price_distributor", 0))),
                    price_customer=Decimal(str(row.get("price_customer", 0))),
                    currency=str(row.get("currency", "ARS")),
                    stock=int(row.get("stock", 0)),
                    active=bool(row.get("active", False)),
                )
                products.append(product)
            except Exception as e:
                logger.warning(f"Error parsing product row: {row}, error: {e}")
                continue

        return products
    except Exception as e:
        logger.error(f"Error reading catalog sheet: {e}")
        return []


def search_products(query: str, vet_id: Optional[str] = None) -> list[Product]:
    """
    Busca productos por nombre/descripción.
    Solo devuelve productos con stock > 0.

    Búsqueda flexible:
    - Divide el query en palabras
    - Encuentra productos que contengan AL MENOS una palabra
    - Ordena por relevancia (más palabras coincidentes = más arriba)
    """
    catalog = get_catalog(vet_id=vet_id, active_only=True)

    # Dividir query en palabras (ignorar palabras muy cortas)
    query_words = [w.lower() for w in query.strip().split() if len(w) >= 2]

    if not query_words:
        return []

    results_with_score = []
    for product in catalog:
        if not product.has_stock:
            continue

        # Buscar en nombre, presentación, descripción y SKU
        searchable = " ".join(filter(None, [
            product.product_name,
            product.presentation,
            product.description,
            product.sku,
        ])).lower()

        # Contar cuántas palabras del query aparecen en el producto
        matches = sum(1 for word in query_words if word in searchable)

        if matches > 0:
            results_with_score.append((product, matches))

    # Ordenar por relevancia (más matches primero)
    results_with_score.sort(key=lambda x: x[1], reverse=True)

    return [product for product, score in results_with_score]


def get_product_by_sku(sku: str) -> Optional[Product]:
    """Obtiene un producto por SKU."""
    catalog = get_catalog(active_only=False)
    for product in catalog:
        if product.sku == sku:
            return product
    return None


def update_product_stock(sku: str, new_stock: int) -> bool:
    """Actualiza el stock de un producto."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_catalog)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("sku", "")) == sku:
                headers = ws.row_values(1)
                col_stock = headers.index("stock") + 1
                col_updated_at = headers.index("updated_at") + 1

                ws.update_cell(i, col_stock, new_stock)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Updated stock for SKU {sku}: {new_stock}")
                return True

        logger.warning(f"Product {sku} not found for stock update")
        return False
    except Exception as e:
        logger.error(f"Error updating product stock: {e}")
        return False


def update_product_price(sku: str, new_price_customer: float, new_price_distributor: float = None) -> bool:
    """Actualiza el precio de un producto en el catálogo."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_catalog)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("sku", "")) == sku:
                headers = ws.row_values(1)
                col_price_customer = headers.index("price_customer") + 1
                ws.update_cell(i, col_price_customer, new_price_customer)

                if new_price_distributor is not None:
                    col_price_dist = headers.index("price_distributor") + 1
                    ws.update_cell(i, col_price_dist, new_price_distributor)

                col_updated = headers.index("updated_at") + 1 if "updated_at" in headers else None
                if col_updated:
                    ws.update_cell(i, col_updated, datetime.utcnow().isoformat())

                logger.info(f"Updated price for SKU {sku}: customer=${new_price_customer}")
                return True

        logger.warning(f"Product {sku} not found for price update")
        return False
    except Exception as e:
        logger.error(f"Error updating product price: {e}")
        return False


def update_shipping_zone_price(zone: str, new_price: float) -> bool:
    """Actualiza el costo de envío de una zona AMBA."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_shipping)
        records = ws.get_all_records()
        zone_normalized = zone.strip().lower()

        for i, row in enumerate(records, start=2):
            zona_sheet = str(row.get("Zona") or row.get("zona") or "").strip().lower()
            if zona_sheet == zone_normalized:
                headers = ws.row_values(1)
                col_precio = headers.index("Precio") + 1 if "Precio" in headers else headers.index("precio") + 1
                ws.update_cell(i, col_precio, new_price)
                logger.info(f"Updated shipping cost for zone '{zone}': ${new_price}")
                return True

        logger.warning(f"Shipping zone '{zone}' not found for price update")
        return False
    except Exception as e:
        logger.error(f"Error updating shipping zone price: {e}")
        return False


# ===========================================
# ORDERS
# ===========================================

def create_order_record(order: Order) -> bool:
    """Crea un registro de pedido en el sheet."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)

        # Preparar datos
        row = [
            order.order_id,
            order.vet_id,
            order.customer.name,
            order.customer.lastname,
            order.customer.email,
            order.customer.whatsapp_e164,
            order.delivery.mode.value,
            order.delivery.address or "",
            order.delivery.zone or "",  # Zona AMBA para envío
            json.dumps([item.model_dump() for item in order.items], default=str),
            str(order.subtotal),
            str(order.shipping_cost),
            str(order.total_amount),
            order.currency,
            order.status.value,
            order.payment_method.value if order.payment_method else "",
            order.mp_preference_id or "",
            order.mp_payment_id or "",
            order.mp_status.value if order.mp_status else "",
            order.external_reference or "",
            order.created_at.isoformat(),
            order.updated_at.isoformat(),
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Created order record: {order.order_id}")
        return True
    except Exception as e:
        logger.error(f"Error creating order record: {e}")
        return False


def get_order_by_id(order_id: str) -> Optional[Order]:
    """Obtiene un pedido por ID."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for row in records:
            if str(row.get("order_id", "")) == order_id:
                return _parse_order_row(row)

        return None
    except Exception as e:
        logger.error(f"Error getting order {order_id}: {e}")
        return None


def get_order_by_external_reference(external_reference: str) -> Optional[Order]:
    """Obtiene un pedido por external_reference."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for row in records:
            if str(row.get("external_reference", "")) == external_reference:
                return _parse_order_row(row)

        return None
    except Exception as e:
        logger.error(f"Error getting order by ref {external_reference}: {e}")
        return None


def update_order_payment_status(
    order_id: str,
    mp_payment_id: str,
    mp_status: MPPaymentStatus,
    status: OrderStatus,
) -> bool:
    """Actualiza el estado de pago de un pedido."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("order_id", "")) == order_id:
                headers = ws.row_values(1)
                col_mp_payment_id = headers.index("mp_payment_id") + 1
                col_mp_status = headers.index("mp_status") + 1
                col_status = headers.index("status") + 1
                col_updated_at = headers.index("updated_at") + 1

                ws.update_cell(i, col_mp_payment_id, mp_payment_id)
                ws.update_cell(i, col_mp_status, mp_status.value)
                ws.update_cell(i, col_status, status.value)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Updated order {order_id} payment status: {mp_status.value}")
                return True

        logger.warning(f"Order {order_id} not found for payment update")
        return False
    except Exception as e:
        logger.error(f"Error updating order payment status: {e}")
        return False


def update_order_status(order_id: str, new_status: OrderStatus) -> bool:
    """
    Actualiza solo el estado de un pedido.

    Usado cuando el vet cambia el estado via el agente
    (ej: marcar como "listo para retirar").
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("order_id", "")) == order_id:
                headers = ws.row_values(1)
                col_status = headers.index("status") + 1
                col_updated_at = headers.index("updated_at") + 1

                ws.update_cell(i, col_status, new_status.value)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Updated order {order_id} status to: {new_status.value}")
                return True

        logger.warning(f"Order {order_id} not found for status update")
        return False
    except Exception as e:
        logger.error(f"Error updating order status: {e}")
        return False


def set_order_payment_method(order_id: str, payment_method: str, new_status: OrderStatus) -> bool:
    """
    Establece el método de pago de un pedido y actualiza su estado.

    Args:
        order_id: ID del pedido
        payment_method: "MERCADOPAGO" o "AT_VET"
        new_status: Nuevo estado del pedido

    Returns:
        True si se actualizó correctamente
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("order_id", "")) == order_id:
                headers = ws.row_values(1)

                # Buscar o agregar columna payment_method
                if "payment_method" in headers:
                    col_payment_method = headers.index("payment_method") + 1
                else:
                    # Si no existe la columna, agregarla al final
                    col_payment_method = len(headers) + 1
                    ws.update_cell(1, col_payment_method, "payment_method")

                col_status = headers.index("status") + 1
                col_updated_at = headers.index("updated_at") + 1

                ws.update_cell(i, col_payment_method, payment_method)
                ws.update_cell(i, col_status, new_status.value)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Set order {order_id} payment method: {payment_method}, status: {new_status.value}")
                return True

        logger.warning(f"Order {order_id} not found for payment method update")
        return False
    except Exception as e:
        logger.error(f"Error setting order payment method: {e}")
        return False


def update_order_preference(order_id: str, preference_id: str, external_reference: str) -> bool:
    """
    Actualiza la preferencia de pago de un pedido.

    También establece el método de pago como MERCADOPAGO y el estado como PAYMENT_PENDING_MP.
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("order_id", "")) == order_id:
                headers = ws.row_values(1)
                col_mp_preference_id = headers.index("mp_preference_id") + 1
                col_external_reference = headers.index("external_reference") + 1
                col_status = headers.index("status") + 1
                col_updated_at = headers.index("updated_at") + 1

                # Buscar o agregar columna payment_method
                if "payment_method" in headers:
                    col_payment_method = headers.index("payment_method") + 1
                else:
                    col_payment_method = len(headers) + 1
                    ws.update_cell(1, col_payment_method, "payment_method")

                ws.update_cell(i, col_mp_preference_id, preference_id)
                ws.update_cell(i, col_external_reference, external_reference)
                ws.update_cell(i, col_payment_method, "MERCADOPAGO")
                ws.update_cell(i, col_status, OrderStatus.PAYMENT_PENDING_MP.value)
                ws.update_cell(i, col_updated_at, datetime.utcnow().isoformat())

                logger.info(f"Updated order {order_id} with preference {preference_id}, payment method: MERCADOPAGO")
                return True

        logger.warning(f"Order {order_id} not found for preference update")
        return False
    except Exception as e:
        logger.error(f"Error updating order preference: {e}")
        return False


def _parse_order_row(row: dict) -> Order:
    """Parsea una fila del sheet a Order."""
    # La columna se llama "items", no "items_json"
    items_json = row.get("items") or row.get("items_json") or "[]"
    items_data = json.loads(items_json) if items_json else []

    items = [
        CartItem(
            product_sku=item["product_sku"],
            product_name=item["product_name"],
            quantity=item["quantity"],
            unit_price=Decimal(str(item["unit_price"])),
            currency=item.get("currency", "ARS"),
        )
        for item in items_data
    ]

    mp_status_str = row.get("mp_status", "")
    mp_status = MPPaymentStatus(mp_status_str) if mp_status_str else None

    payment_method_str = row.get("payment_method", "")
    payment_method = PaymentMethod(payment_method_str) if payment_method_str else None

    # Calcular subtotal si no existe (backwards compatibility)
    subtotal = Decimal(str(row.get("subtotal", 0)))
    shipping_cost = Decimal(str(row.get("shipping_cost", 0)))
    total_amount = Decimal(str(row.get("total_amount", 0)))

    # Si subtotal es 0 pero total_amount existe, usar total_amount como subtotal
    if subtotal == 0 and total_amount > 0:
        subtotal = total_amount

    return Order(
        order_id=str(row.get("order_id", "")),
        vet_id=str(row.get("vet_id", "")),
        customer=CustomerData(
            name=str(row.get("customer_name", "")),
            lastname=str(row.get("customer_lastname", "")),
            email=str(row.get("customer_email", "")),
            whatsapp_e164=normalize_phone(row.get("customer_whatsapp_e164", "")),
        ),
        delivery=DeliveryData(
            mode=DeliveryMode(row.get("delivery_mode", "PICKUP")),
            address=str(row.get("delivery_address", "")) or None,
            zone=str(row.get("delivery_zone", "")) or None,
        ),
        items=items,
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        total_amount=total_amount,
        currency=str(row.get("currency", "ARS")),
        status=OrderStatus(row.get("status", "CREATED")),
        payment_method=payment_method,
        mp_preference_id=str(row.get("mp_preference_id", "")) or None,
        mp_payment_id=str(row.get("mp_payment_id", "")) or None,
        mp_status=mp_status,
        external_reference=str(row.get("external_reference", "")) or None,
        created_at=_parse_datetime(str(row.get("created_at", ""))),
        updated_at=_parse_datetime(str(row.get("updated_at", ""))),
    )


# ===========================================
# EVENTS
# ===========================================

def log_event(
    event_type: EventType,
    order_id: Optional[str] = None,
    vet_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> bool:
    """Registra un evento de auditoría."""
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_events)

        import uuid
        event_id = f"EVT-{uuid.uuid4().hex[:8].upper()}"

        row = [
            event_id,
            order_id or "",
            vet_id or "",
            event_type.value,
            json.dumps(payload or {}, default=str),
            datetime.utcnow().isoformat(),
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.debug(f"Logged event: {event_type.value} for order {order_id}")
        return True
    except Exception as e:
        logger.error(f"Error logging event: {e}")
        return False


# =============================================================================
# COSTO DE ENVÍO
# =============================================================================


def _parse_bool(value) -> bool:
    """Parsea booleanos de Sheets que pueden venir como bool, 'TRUE'/'FALSE', 1/0."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().upper() in ("TRUE", "1", "YES", "SI", "SÍ")


def _parse_price(price_value) -> Decimal:
    """Parsea un precio que puede venir con formato ($1,234.56)."""
    if price_value is None:
        return Decimal("0")

    price_str = str(price_value)
    # Remover $, espacios, y separadores de miles
    price_str = price_str.replace("$", "").replace(" ", "").replace(",", "").strip()

    if not price_str:
        return Decimal("0")

    try:
        return Decimal(price_str)
    except Exception:
        return Decimal("0")


def get_shipping_cost(zone: str) -> Optional[Decimal]:
    """
    Obtiene el costo de envío para una zona AMBA.

    Args:
        zone: Nombre de la localidad/zona (ej: "CABA", "San Isidro", "La Plata")

    Returns:
        Costo de envío como Decimal, o None si la zona no existe
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_shipping)
        records = ws.get_all_records()

        # Normalizar zona para comparación (case insensitive, sin espacios extra)
        zone_normalized = zone.strip().lower()

        logger.debug(f"Searching for zone: '{zone_normalized}' in {len(records)} records")

        for row in records:
            # Intentar varios nombres de columna para la zona
            zona_sheet = str(row.get("Zona") or row.get("zona") or row.get("ZONA") or "").strip().lower()

            if zona_sheet == zone_normalized:
                # Intentar varios nombres de columna para el precio
                precio_raw = row.get("Precio") or row.get("precio") or row.get("PRECIO") or 0
                precio = _parse_price(precio_raw)
                logger.info(f"Found shipping cost for '{zone}': ${precio}")
                return precio

        logger.warning(f"Shipping zone not found: '{zone}'. Available zones: {[str(r.get('Zona', r.get('zona', ''))) for r in records[:5]]}...")
        return None
    except Exception as e:
        logger.error(f"Error getting shipping cost: {e}")
        return None


def get_all_shipping_zones() -> list[dict]:
    """
    Obtiene todas las zonas de envío disponibles con sus precios.

    Returns:
        Lista de diccionarios con zona y precio
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_shipping)
        records = ws.get_all_records()

        zones = []
        for row in records:
            zona = str(row.get("Zona", row.get("zona", ""))).strip()
            precio_raw = row.get("Precio") or row.get("precio") or row.get("PRECIO") or 0
            precio = _parse_price(precio_raw)
            if zona:
                zones.append({
                    "zone": zona,
                    "price": precio,
                })

        return zones
    except Exception as e:
        logger.error(f"Error getting shipping zones: {e}")
        return []


# =============================================================================
# VETS — CRUD (backoffice)
# =============================================================================


def create_vet(
    name: str,
    whatsapp_e164: str,
    contact_name: Optional[str] = None,
    address: Optional[str] = None,
    email: Optional[str] = None,
    distributor_id: Optional[str] = None,
) -> Optional[VetContext]:
    """
    Crea una nueva veterinaria en el sheet.

    Genera un vet_id único (VET-XXXXXX) y escribe la fila completa.
    """
    settings = get_settings()
    try:
        import uuid as _uuid
        vet_id = f"VET-{_uuid.uuid4().hex[:6].upper()}"
        phone = normalize_phone(whatsapp_e164)
        now = datetime.utcnow().isoformat()

        ws = get_worksheet(settings.sheet_vets)
        headers = ws.row_values(1)

        row_dict = {
            "vet_id": vet_id,
            "name": name.strip(),
            "whatsapp_e164": phone,
            "active": True,
            "mp_connected": False,
            "mp_user_id": "",
            "contact_name": contact_name or "",
            "address": address or "",
            "email": email or "",
            "distributor_id": distributor_id or "",
            "created_at": now,
            "updated_at": now,
        }
        row = [row_dict.get(h, "") for h in headers]
        ws.append_row(row, value_input_option="USER_ENTERED")

        logger.info(f"Created vet: {vet_id} — {name}")
        return VetContext(
            vet_id=vet_id,
            name=name.strip(),
            whatsapp_e164=phone,
            active=True,
            mp_connected=False,
            contact_name=contact_name,
            address=address,
            email=email,
            distributor_id=distributor_id,
        )
    except Exception as e:
        logger.error(f"Error creating vet: {e}")
        return None


def update_vet(
    vet_id: str,
    name: Optional[str] = None,
    whatsapp_e164: Optional[str] = None,
    active: Optional[bool] = None,
    contact_name: Optional[str] = None,
    address: Optional[str] = None,
    email: Optional[str] = None,
    distributor_id: Optional[str] = None,
) -> bool:
    """
    Actualiza campos de una veterinaria existente.
    Solo actualiza los campos que no son None.
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_vets)
        records = ws.get_all_records()
        headers = ws.row_values(1)

        for i, row in enumerate(records, start=2):
            if str(row.get("vet_id", "")) != vet_id:
                continue

            updates = {}
            if name is not None:
                updates["name"] = name.strip()
            if whatsapp_e164 is not None:
                updates["whatsapp_e164"] = normalize_phone(whatsapp_e164)
            if active is not None:
                updates["active"] = active
            if contact_name is not None:
                updates["contact_name"] = contact_name
            if address is not None:
                updates["address"] = address
            if email is not None:
                updates["email"] = email
            if distributor_id is not None:
                updates["distributor_id"] = distributor_id
            updates["updated_at"] = datetime.utcnow().isoformat()

            for field, value in updates.items():
                if field in headers:
                    col = headers.index(field) + 1
                    ws.update_cell(i, col, value)

            logger.info(f"Updated vet {vet_id}: {list(updates.keys())}")
            return True

        logger.warning(f"Vet {vet_id} not found for update")
        return False
    except Exception as e:
        logger.error(f"Error updating vet {vet_id}: {e}")
        return False


# =============================================================================
# CATALOG — UPSERT (backoffice)
# =============================================================================


def upsert_product(
    sku: str,
    product_name: str,
    price_customer: Decimal,
    price_distributor: Decimal,
    stock: int,
    ean: Optional[str] = None,
    presentation: Optional[str] = None,
    description: Optional[str] = None,
    currency: str = "ARS",
    active: bool = True,
) -> dict:
    """
    Crea o actualiza un producto en el catálogo por SKU.

    Returns:
        dict con action ('created' | 'updated') y el product dict.
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_catalog)
        records = ws.get_all_records()
        headers = ws.row_values(1)
        now = datetime.utcnow().isoformat()

        # Buscar si existe por SKU
        for i, row in enumerate(records, start=2):
            if str(row.get("sku", "")).strip().upper() == sku.strip().upper():
                # UPDATE
                updates = {
                    "product_name": product_name.strip(),
                    "price_customer": float(price_customer),
                    "price_distributor": float(price_distributor),
                    "stock": int(stock),
                    "active": active,
                    "updated_at": now,
                }
                if ean is not None:
                    updates["ean"] = ean
                if presentation is not None:
                    updates["presentation"] = presentation
                if description is not None:
                    updates["description"] = description

                for field, value in updates.items():
                    if field in headers:
                        col = headers.index(field) + 1
                        ws.update_cell(i, col, value)

                logger.info(f"Updated product SKU {sku}")
                return {"action": "updated", "sku": sku}

        # CREATE
        row_dict = {
            "sku": sku.strip().upper(),
            "ean": ean or "",
            "product_name": product_name.strip(),
            "presentation": presentation or "",
            "description": description or "",
            "price_distributor": float(price_distributor),
            "price_customer": float(price_customer),
            "currency": currency,
            "stock": int(stock),
            "active": active,
            "created_at": now,
            "updated_at": now,
        }
        row = [row_dict.get(h, "") for h in headers]
        ws.append_row(row, value_input_option="USER_ENTERED")

        logger.info(f"Created product SKU {sku}")
        return {"action": "created", "sku": sku}

    except Exception as e:
        logger.error(f"Error upserting product {sku}: {e}")
        return {"action": "error", "error": str(e)}


# =============================================================================
# ORDERS — LIST (backoffice)
# =============================================================================


def get_all_orders(
    vet_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
) -> list[Order]:
    """
    Lista pedidos con filtros opcionales.

    Args:
        vet_id: Filtrar por veterinaria
        status: Filtrar por estado (string del enum)
        search: Buscar en order_id, nombre o email del cliente
        limit: Máximo de resultados

    Returns:
        Lista de Order ordenada por fecha desc.
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_orders)
        records = ws.get_all_records()

        orders = []
        search_lower = search.strip().lower() if search else None

        for row in records:
            try:
                # Filtro por vet
                if vet_id and str(row.get("vet_id", "")) != vet_id:
                    continue

                # Filtro por status
                if status and str(row.get("status", "")) != status:
                    continue

                # Filtro por búsqueda libre
                if search_lower:
                    searchable = " ".join([
                        str(row.get("order_id", "")),
                        str(row.get("customer_name", "")),
                        str(row.get("customer_lastname", "")),
                        str(row.get("customer_email", "")),
                        str(row.get("customer_whatsapp_e164", "")),
                    ]).lower()
                    if search_lower not in searchable:
                        continue

                orders.append(_parse_order_row(row))
            except Exception as e:
                logger.warning(f"Error parsing order row: {e}")
                continue

        orders.sort(key=lambda o: o.created_at, reverse=True)
        return orders[:limit]

    except Exception as e:
        logger.error(f"Error listing orders: {e}")
        return []


# =============================================================================
# CUSTOMERS — LIST ALL (backoffice, sin filtro de vet)
# =============================================================================


def get_all_customers(
    vet_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
) -> list[Customer]:
    """
    Lista clientes con filtros opcionales.

    Args:
        vet_id: Filtrar por veterinaria (None = todos)
        search: Buscar en nombre, apellido, email, teléfono
        limit: Máximo de resultados
    """
    settings = get_settings()
    try:
        ws = get_worksheet(settings.sheet_customers)
        records = ws.get_all_records()

        customers = []
        search_lower = search.strip().lower() if search else None

        for row in records:
            try:
                if vet_id and str(row.get("vet_id", "")) != vet_id:
                    continue
                if not row.get("active", True):
                    continue

                if search_lower:
                    searchable = " ".join([
                        str(row.get("name", "")),
                        str(row.get("lastname", "")),
                        str(row.get("email", "")),
                        str(row.get("whatsapp_e164", "")),
                    ]).lower()
                    if search_lower not in searchable:
                        continue

                customers.append(Customer(
                    customer_id=str(row.get("customer_id", "")),
                    vet_id=str(row.get("vet_id", "")),
                    name=str(row.get("name", "")),
                    lastname=str(row.get("lastname", "")),
                    email=str(row.get("email", "")),
                    whatsapp_e164=normalize_phone(row.get("whatsapp_e164", "")),
                    address=str(row.get("address", "")) or None,
                    pet_type=str(row.get("pet_type", "")) or None,
                    pet_name=str(row.get("pet_name", "")) or None,
                    notes=str(row.get("notes", "")) or None,
                    active=bool(row.get("active", True)),
                ))
            except Exception as e:
                logger.warning(f"Error parsing customer row: {e}")
                continue

        return customers[:limit]
    except Exception as e:
        logger.error(f"Error listing customers: {e}")
        return []
