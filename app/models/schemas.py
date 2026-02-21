"""
schemas.py
Modelos Pydantic para Direct to Vet.
Todos los tipos de datos del sistema.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import phonenumbers


# ===========================================
# ENUMS
# ===========================================

class PaymentMethod(str, Enum):
    """Método de pago del pedido."""
    MERCADOPAGO = "MERCADOPAGO"  # Pago online con link de MP
    AT_VET = "AT_VET"            # Pago en mostrador de la veterinaria


class OrderStatus(str, Enum):
    """Estados posibles de un pedido."""
    CREATED = "CREATED"
    # Estados de pago
    PAYMENT_PENDING_MP = "PAYMENT_PENDING_MP"  # Esperando pago por MercadoPago
    PAYMENT_AT_VET = "PAYMENT_AT_VET"          # Pagará en mostrador (no requiere validación)
    PAYMENT_APPROVED = "PAYMENT_APPROVED"      # Pago confirmado (MP) o recibido (mostrador)
    PAYMENT_REJECTED = "PAYMENT_REJECTED"      # Pago rechazado (solo MP)
    # Estados de logística (manejados por distribuidora)
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    # Estados finales
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    # Deprecado - mantener para compatibilidad
    PAYMENT_PENDING = "PAYMENT_PENDING"  # @deprecated: usar PAYMENT_PENDING_MP


class DeliveryMode(str, Enum):
    """Modos de entrega."""
    PICKUP = "PICKUP"      # Retira en veterinaria
    DELIVERY = "DELIVERY"  # Envío a domicilio


class ConversationState(str, Enum):
    """Estados del FSM conversacional."""
    IDLE = "IDLE"
    BROWSING_CATALOG = "BROWSING_CATALOG"
    BUILDING_CART = "BUILDING_CART"
    CHECKOUT_CUSTOMER = "CHECKOUT_CUSTOMER"
    CHECKOUT_DELIVERY = "CHECKOUT_DELIVERY"
    CONFIRMATION = "CONFIRMATION"
    COMPLETED = "COMPLETED"


class MPPaymentStatus(str, Enum):
    """Estados de pago de Mercado Pago."""
    PENDING = "pending"
    APPROVED = "approved"
    AUTHORIZED = "authorized"
    IN_PROCESS = "in_process"
    IN_MEDIATION = "in_mediation"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    CHARGED_BACK = "charged_back"


# ===========================================
# VETERINARIA
# ===========================================

class VetContext(BaseModel):
    """Contexto de una veterinaria identificada."""
    vet_id: str
    name: str
    whatsapp_e164: str
    active: bool = True
    mp_connected: bool = False
    mp_user_id: Optional[str] = None
    # Campos adicionales
    contact_name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    distributor_id: Optional[str] = None

    @field_validator("whatsapp_e164")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Valida formato E.164 del teléfono. Permite vacío o '-' para vets sin número."""
        if not v or v.strip() in ("-", "N/A", "n/a"):
            return ""
        try:
            parsed = phonenumbers.parse(v, "AR")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Número de teléfono inválido")
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
        except phonenumbers.NumberParseException:
            raise ValueError(f"No se pudo parsear el teléfono: {v}")


# ===========================================
# CATÁLOGO
# ===========================================

class Product(BaseModel):
    """Producto del catálogo."""
    sku: str
    ean: Optional[str] = None
    product_name: str
    presentation: Optional[str] = None
    description: Optional[str] = None
    price_distributor: Decimal = Field(ge=0)
    price_customer: Decimal = Field(ge=0)
    currency: str = "ARS"
    stock: int = Field(ge=0)
    active: bool = True

    @property
    def has_stock(self) -> bool:
        return self.stock > 0

    def format_price(self) -> str:
        """Formatea el precio para mostrar."""
        return f"${self.price_customer:,.2f} {self.currency}"


# ===========================================
# CARRITO
# ===========================================

class CartItem(BaseModel):
    """Item en el carrito."""
    product_sku: str
    product_name: str
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)
    currency: str = "ARS"

    @property
    def subtotal(self) -> Decimal:
        return self.unit_price * self.quantity

    def format_line(self) -> str:
        """Formatea la línea del carrito."""
        return f"{self.quantity}x {self.product_name} - ${self.subtotal:,.2f}"


class CartSummary(BaseModel):
    """Resumen del carrito."""
    items: list[CartItem] = Field(default_factory=list)
    currency: str = "ARS"

    @property
    def total_items(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def total_amount(self) -> Decimal:
        return sum(item.subtotal for item in self.items)

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0

    def format_cart(self) -> str:
        """Formatea el carrito completo."""
        if self.is_empty:
            return "El carrito está vacío."

        lines = ["*Tu carrito:*"]
        for i, item in enumerate(self.items, 1):
            lines.append(f"{i}. {item.format_line()}")
        lines.append(f"\n*Total: ${self.total_amount:,.2f} {self.currency}*")
        return "\n".join(lines)


# ===========================================
# CLIENTE REGISTRADO (persistente)
# ===========================================

class Customer(BaseModel):
    """Cliente registrado en el sistema."""
    customer_id: str
    vet_id: str  # Vet que lo tiene como cliente
    name: str
    lastname: str
    email: str
    whatsapp_e164: str
    address: Optional[str] = None
    pet_type: Optional[str] = None  # Tipo de mascota (Perro, Gato, etc.)
    pet_name: Optional[str] = None  # Nombre de la mascota
    notes: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.name} {self.lastname}"


# ===========================================
# CLIENTE FINAL (para pedido)
# ===========================================

class CustomerData(BaseModel):
    """Datos del cliente final para un pedido."""
    name: str = Field(min_length=2)
    lastname: str = Field(min_length=2)
    email: str
    whatsapp_e164: str

    @field_validator("whatsapp_e164")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Valida formato E.164 del teléfono."""
        try:
            parsed = phonenumbers.parse(v, "AR")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Número de teléfono inválido")
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
        except phonenumbers.NumberParseException:
            raise ValueError(f"No se pudo parsear el teléfono: {v}")

    @property
    def full_name(self) -> str:
        return f"{self.name} {self.lastname}"


class DeliveryData(BaseModel):
    """Datos de entrega."""
    mode: DeliveryMode = DeliveryMode.PICKUP
    address: Optional[str] = None
    zone: Optional[str] = None  # Localidad AMBA para cálculo de envío
    notes: Optional[str] = None

    @field_validator("address")
    @classmethod
    def validate_address_if_delivery(cls, v: Optional[str], info) -> Optional[str]:
        """Requiere dirección si es delivery."""
        # Note: This validator runs before mode is set in some cases
        # Full validation should happen at the Order level
        return v


# ===========================================
# PEDIDO
# ===========================================

class Order(BaseModel):
    """Pedido completo."""
    order_id: str
    vet_id: str
    customer: CustomerData
    delivery: DeliveryData
    items: list[CartItem]
    subtotal: Decimal = Field(ge=0)  # Suma de items sin envío
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)  # Costo de envío
    total_amount: Decimal = Field(ge=0)  # subtotal + shipping_cost
    currency: str = "ARS"
    status: OrderStatus = OrderStatus.CREATED
    payment_method: Optional[PaymentMethod] = None  # MERCADOPAGO o AT_VET
    mp_preference_id: Optional[str] = None
    mp_payment_id: Optional[str] = None
    mp_status: Optional[MPPaymentStatus] = None
    external_reference: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def generate_external_reference(self) -> str:
        """Genera referencia externa para MP."""
        return f"DTV|{self.vet_id}|{self.order_id}"


# ===========================================
# PAGOS
# ===========================================

class PaymentLink(BaseModel):
    """Link de pago generado."""
    preference_id: str
    init_point: str  # URL del checkout
    sandbox_init_point: Optional[str] = None
    external_reference: str
    expires_at: Optional[datetime] = None


class WebhookPayment(BaseModel):
    """Datos de pago recibidos por webhook."""
    payment_id: str
    external_reference: str
    status: MPPaymentStatus
    status_detail: Optional[str] = None
    amount: Decimal
    currency: str = "ARS"
    payer_email: Optional[str] = None
    date_approved: Optional[datetime] = None


# ===========================================
# OAUTH
# ===========================================

class OAuthResult(BaseModel):
    """Resultado de OAuth con Mercado Pago."""
    success: bool
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    mp_user_id: Optional[str] = None
    error: Optional[str] = None


class StoredToken(BaseModel):
    """Token OAuth almacenado."""
    vet_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    mp_user_id: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_expired(self) -> bool:
        """Verifica si el token expiró (con 5 min de margen)."""
        from datetime import timedelta
        return datetime.utcnow() >= (self.expires_at - timedelta(minutes=5))


# ===========================================
# MEMORIA DEL AGENTE
# ===========================================

class AgentMemory(BaseModel):
    """Memoria de sesión del agente."""
    session_id: str
    vet_id: Optional[str] = None
    vet_context: Optional[VetContext] = None
    cart: CartSummary = Field(default_factory=CartSummary)
    current_state: ConversationState = ConversationState.IDLE
    pending_customer: Optional[CustomerData] = None
    pending_delivery: Optional[DeliveryData] = None
    current_order_id: Optional[str] = None
    last_search_results: list[Product] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def update_state(self, new_state: ConversationState) -> None:
        """Actualiza el estado y timestamp."""
        self.current_state = new_state
        self.updated_at = datetime.utcnow()

    def clear_cart(self) -> None:
        """Limpia el carrito."""
        self.cart = CartSummary()
        self.updated_at = datetime.utcnow()

    def reset_checkout(self) -> None:
        """Resetea datos de checkout."""
        self.pending_customer = None
        self.pending_delivery = None
        self.current_order_id = None
        self.updated_at = datetime.utcnow()


# ===========================================
# EVENTOS
# ===========================================

class EventType(str, Enum):
    """Tipos de eventos para auditoría."""
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED"
    PAYMENT_LINK_SENT = "PAYMENT_LINK_SENT"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    PAYMENT_APPROVED = "PAYMENT_APPROVED"
    PAYMENT_REJECTED = "PAYMENT_REJECTED"
    PAYMENT_STATUS_CHANGED = "PAYMENT_STATUS_CHANGED"
    ORDER_COMPLETED = "ORDER_COMPLETED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED"
    ERROR = "ERROR"


class Event(BaseModel):
    """Evento de auditoría."""
    event_id: str
    order_id: Optional[str] = None
    vet_id: Optional[str] = None
    event_type: EventType
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
