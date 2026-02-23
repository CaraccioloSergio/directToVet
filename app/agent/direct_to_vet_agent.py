"""
direct_to_vet_agent.py
Agente principal de Direct to Vet usando Google ADK.
"""

import os
from google.adk.agents import Agent  # type: ignore
from google.genai import Client  # type: ignore

from app.tools.identity import identify_veterinarian
from app.tools.catalog import search_catalog
from app.tools.cart import add_to_cart, view_cart, clear_cart
from app.tools.orders import create_order, get_order_status, cancel_order, set_payment_method, get_shipping_cost
from app.tools.customers import search_customer, search_order, register_customer, update_customer_info
from app.tools.oauth_mp import start_mp_oauth, check_mp_connection
from app.tools.payments import create_payment_link, get_payment_link_for_order
from app.tools.messaging import send_payment_link_to_customer
from app.agent.prompts import AGENT_DESCRIPTION, AGENT_INSTRUCTIONS
from app.config import get_settings

settings = get_settings()

# =============================================================================
# CONFIGURAR GOOGLE AI CLIENT
# =============================================================================

# Asegurar que la API key esté en el entorno
if settings.google_api_key:
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key

# =============================================================================
# DEFINICIÓN DEL AGENTE ADK
# =============================================================================

root_agent = Agent(
    name="direct_to_vet_agent",
    model=settings.gemini_model,
    description=AGENT_DESCRIPTION,
    instruction=AGENT_INSTRUCTIONS,
    tools=[
        # Identificación
        identify_veterinarian,

        # Clientes
        search_customer,
        register_customer,
        update_customer_info,

        # Catálogo
        search_catalog,

        # Carrito
        add_to_cart,
        view_cart,
        clear_cart,

        # Pedidos
        create_order,
        get_order_status,
        cancel_order,
        search_order,
        set_payment_method,
        get_shipping_cost,

        # OAuth Mercado Pago
        start_mp_oauth,
        check_mp_connection,

        # Pagos
        create_payment_link,
        get_payment_link_for_order,

        # Mensajería
        send_payment_link_to_customer,
    ],
)
