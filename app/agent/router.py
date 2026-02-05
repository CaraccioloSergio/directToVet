"""
router.py
Router para procesar mensajes entrantes y coordinar con el agente.
"""

import logging
from typing import Optional

from google.adk.agents import Agent  # type: ignore
from google.adk.runners import Runner  # type: ignore
from google.adk.sessions import InMemorySessionService  # type: ignore
from google.genai import types  # type: ignore

from app.agent.direct_to_vet_agent import root_agent
from app.agent.memory import (
    get_session_store,
    generate_session_id,
)
from app.agent.prompts import CUSTOMER_INSTRUCTIONS
from app.tools.identity import identify_role, UserRole
from app.tools.customers import get_my_orders
from app.tools.messaging import send_whatsapp_message
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Servicio de sesiones ADK (en memoria para PoC)
session_service = InMemorySessionService()

# Runner del agente
APP_NAME = "direct_to_vet"
USER_ID_PREFIX_VET = "vet_"
USER_ID_PREFIX_CUSTOMER = "customer_"


# =============================================================================
# AGENTE PARA CLIENTES (RESTRINGIDO)
# =============================================================================

customer_agent = Agent(
    name="direct_to_vet_customer_agent",
    model=settings.gemini_model,
    description="Agente de consulta para clientes finales de Direct to Vet.",
    instruction=CUSTOMER_INSTRUCTIONS,
    tools=[
        # Solo puede consultar sus pedidos
        get_my_orders,
    ],
)


# =============================================================================
# PROCESAMIENTO DE MENSAJES
# =============================================================================


async def process_incoming_message(
    phone_e164: str,
    message_text: str,
    message_sid: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> str:
    """
    Procesa un mensaje entrante de WhatsApp.

    Identifica el rol del remitente (VET, CUSTOMER, UNKNOWN) y enruta
    al agente correspondiente con las capacidades apropiadas.

    Args:
        phone_e164: Número de WhatsApp del remitente
        message_text: Texto del mensaje
        message_sid: ID del mensaje de Twilio (para trazabilidad)
        profile_name: Nombre del perfil de WhatsApp (si disponible)

    Returns:
        Respuesta del agente (también se envía por WhatsApp)
    """
    try:
        logger.info(f"Processing message from {phone_e164}: {message_text[:50]}...")

        # 1. Identificar rol del remitente
        role_result = identify_role(phone_e164)
        role = role_result["role"]

        logger.info(f"Identified role: {role} for {phone_e164}")

        # 2. Enrutar según rol
        if role == UserRole.VET:
            return await _process_vet_message(
                phone_e164=phone_e164,
                message_text=message_text,
                vet_context=role_result.get("vet_context"),
            )

        elif role == UserRole.CUSTOMER:
            return await _process_customer_message(
                phone_e164=phone_e164,
                message_text=message_text,
                customer_context=role_result.get("customer"),
            )

        else:  # UNKNOWN
            response = (
                "Hola! Tu número no está registrado en Direct to Vet.\n"
                "Si sos una veterinaria, contactá al administrador para darte de alta.\n"
                "Si sos un cliente, contactá a tu veterinaria para más información."
            )
            await _send_response(phone_e164, response)
            return response

    except Exception as e:
        logger.error(f"Error processing message: {e}")

        error_response = (
            "Disculpá, hubo un problema procesando tu mensaje.\n"
            "Intentá de nuevo en unos segundos."
        )
        await _send_response(phone_e164, error_response)

        return error_response


async def _process_vet_message(
    phone_e164: str,
    message_text: str,
    vet_context: dict,
) -> str:
    """
    Procesa un mensaje de una VETERINARIA.

    Tiene acceso completo al agente con todas las capacidades.
    """
    try:
        # Obtener o crear sesión
        session_store = get_session_store()
        session_id = generate_session_id(phone_e164)
        session = session_store.get_or_create(session_id, phone_e164)

        # Actualizar sesión con datos del vet
        session.set_vet(
            vet_id=vet_context["vet_id"],
            name=vet_context["name"],
            mp_connected=vet_context.get("mp_connected", False),
        )

        # Preparar contexto
        context = session.to_context_dict()

        # Construir mensaje enriquecido
        contact_name = vet_context.get('contact_name') or context.get('name', 'No identificado')
        enriched_message = f"""
[CONTEXTO DE SESIÓN]
- Rol: VETERINARIA
- Veterinaria: {context.get('name', 'No identificado')}
- Contacto: {contact_name}
- ID: {context.get('vet_id', 'N/A')}
- MP Conectado: {'Sí' if context.get('mp_connected') else 'No'}
- Dirección vet: {vet_context.get('address') or 'No especificada'}
- Session ID: {session_id}

[MENSAJE DEL VETERINARIO]
{message_text}
"""

        # Ejecutar agente de veterinarias (completo)
        response = await _run_agent(
            agent=root_agent,
            session_id=session_id,
            user_id=f"{USER_ID_PREFIX_VET}{phone_e164}",
            message=enriched_message,
        )

        await _send_response(phone_e164, response)
        logger.info(f"Vet response sent to {phone_e164}")

        return response

    except Exception as e:
        logger.error(f"Error processing vet message: {e}")
        raise


async def _process_customer_message(
    phone_e164: str,
    message_text: str,
    customer_context: dict,
) -> str:
    """
    Procesa un mensaje de un CLIENTE.

    Tiene acceso restringido - solo puede consultar sus pedidos.
    """
    try:
        session_id = f"customer_{phone_e164}"

        # Construir mensaje con contexto de cliente
        enriched_message = f"""
[CONTEXTO DE SESIÓN]
- Rol: CLIENTE
- Nombre: {customer_context.get('name', 'Cliente')}
- WhatsApp: {phone_e164}

[MENSAJE DEL CLIENTE]
{message_text}

[INSTRUCCIÓN INTERNA]
Si el cliente pregunta por su pedido, usá get_my_orders("{phone_e164}") para consultar.
"""

        # Ejecutar agente de clientes (restringido)
        response = await _run_agent(
            agent=customer_agent,
            session_id=session_id,
            user_id=f"{USER_ID_PREFIX_CUSTOMER}{phone_e164}",
            message=enriched_message,
        )

        await _send_response(phone_e164, response)
        logger.info(f"Customer response sent to {phone_e164}")

        return response

    except Exception as e:
        logger.error(f"Error processing customer message: {e}")
        raise


async def _run_agent(
    agent: Agent,
    session_id: str,
    user_id: str,
    message: str,
) -> str:
    """
    Ejecuta un agente con el mensaje dado.

    Args:
        agent: Agente a ejecutar (vet o customer)
        session_id: ID de la sesión
        user_id: ID del usuario
        message: Mensaje a procesar

    Returns:
        Respuesta del agente
    """
    try:
        # Crear sesión siempre (si ya existe, el ADK debería manejar el error)
        try:
            session = await session_service.create_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            logger.info(f"Created ADK session: {session_id}")
        except Exception as e:
            logger.warning(f"Session might already exist: {e}")
            # Intentar obtenerla
            session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            logger.info(f"Got existing ADK session: {session_id}")

        # Crear runner
        runner = Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=session_service,
        )

        # Crear contenido del mensaje
        content = types.Content(
            role="user",
            parts=[types.Part(text=message)],
        )

        # Ejecutar y obtener respuesta
        response_text = ""

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            # Procesar eventos del agente
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text

        return response_text.strip() or "No tengo una respuesta para eso."

    except Exception as e:
        logger.error(f"Error running agent: {e}")
        raise


async def _send_response(phone_e164: str, text: str) -> bool:
    """
    Envía la respuesta por WhatsApp.

    Args:
        phone_e164: Número destino
        text: Texto a enviar

    Returns:
        True si se envió correctamente
    """
    try:
        # Dividir mensajes largos (WhatsApp tiene límite de ~4096 chars)
        max_length = 4000

        if len(text) <= max_length:
            result = send_whatsapp_message(phone_e164, text)
            return result["status"] == "sent"

        # Dividir en chunks
        chunks = [text[i : i + max_length] for i in range(0, len(text), max_length)]

        for chunk in chunks:
            result = send_whatsapp_message(phone_e164, chunk)
            if result["status"] != "sent":
                return False

        return True

    except Exception as e:
        logger.error(f"Error sending response: {e}")
        return False


# =============================================================================
# API para testing sin WhatsApp
# =============================================================================


async def process_test_message(
    vet_id: str,
    message_text: str,
) -> dict:
    """
    Procesa un mensaje de prueba (sin WhatsApp).

    Útil para testing local y debugging.

    Args:
        vet_id: ID del veterinario
        message_text: Texto del mensaje

    Returns:
        dict con la respuesta del agente
    """
    try:
        # Crear sesión de prueba
        session_id = f"test_{vet_id}"
        user_id = f"test_user_{vet_id}"

        # Contexto de prueba
        enriched_message = f"""
[CONTEXTO DE SESIÓN - TEST MODE]
- Rol: VETERINARIA
- Veterinario ID: {vet_id}
- Session ID: {session_id}

[MENSAJE]
{message_text}
"""

        response = await _run_agent(
            agent=root_agent,
            session_id=session_id,
            user_id=user_id,
            message=enriched_message,
        )

        return {
            "status": "success",
            "response": response,
            "session_id": session_id,
        }

    except Exception as e:
        logger.error(f"Error in test message: {e}")
        return {
            "status": "error",
            "message": str(e),
        }
