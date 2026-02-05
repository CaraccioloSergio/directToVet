# Direct to Vet - WhatsApp Agent

Agente conversacional para veterinarias usando **Google ADK**, **WhatsApp (Twilio)** y **Mercado Pago**.

## Qué hace

Permite a veterinarias:
- Consultar catálogo de productos (Royal Canin)
- Armar pedidos para sus clientes
- Registrar clientes con datos de mascotas
- Generar links de pago de Mercado Pago (con envío incluido)
- Enviar links por WhatsApp al cliente final
- Cobrar directamente en la cuenta MP de cada veterinaria (OAuth)
- Recibir mensajes de voz (transcripción automática con Gemini)

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                      VETERINARIO                            │
│                      (WhatsApp)                             │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      TWILIO                                 │
│                  (WhatsApp API)                             │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 DIRECT TO VET AGENT                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Google    │  │   Tools     │  │   Memory    │         │
│  │    ADK      │──│  (Actions)  │──│  (Session)  │         │
│  │   Agent     │  │             │  │             │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│   Google    │   │   Mercado   │   │   Twilio    │
│   Sheets    │   │    Pago     │   │  (Envío)    │
│  (Data)     │   │  (Pagos)    │   │             │
└─────────────┘   └─────────────┘   └─────────────┘
```

## Stack

- **Python 3.11**
- **Google ADK** - Framework de agentes
- **Gemini 2.0 Flash** - Modelo de lenguaje (chat + transcripción de audio)
- **FastAPI** - API REST
- **Twilio** - WhatsApp Business API
- **Mercado Pago** - Pagos con OAuth
- **Google Sheets** - Base de datos (PoC)
- **SendGrid** - Emails operativos (opcional)

## Estructura

```
directToVet/
├── app/
│   ├── agent/
│   │   ├── direct_to_vet_agent.py  # Agente ADK principal
│   │   ├── prompts.py              # Instrucciones del agente
│   │   ├── memory.py               # Memoria de sesión
│   │   └── router.py               # Router de mensajes
│   ├── tools/
│   │   ├── identity.py             # Identificación de vets
│   │   ├── catalog.py              # Búsqueda de productos
│   │   ├── cart.py                 # Gestión de carrito
│   │   ├── orders.py               # Creación de pedidos
│   │   ├── customers.py            # Gestión de clientes
│   │   ├── payments.py             # Links de pago MP
│   │   ├── oauth_mp.py             # OAuth Mercado Pago
│   │   └── messaging.py            # Envío de WhatsApp
│   ├── webhooks/
│   │   ├── twilio.py               # Webhook WhatsApp (texto + audio)
│   │   └── mercadopago.py          # Webhook pagos
│   ├── infra/
│   │   ├── sheets.py               # Google Sheets client
│   │   ├── audio.py                # Transcripción de audio (Gemini)
│   │   ├── token_store.py          # Storage de tokens OAuth
│   │   └── email_service.py        # SendGrid
│   ├── models/
│   │   └── schemas.py              # Modelos Pydantic
│   ├── config.py                   # Configuración
│   └── main.py                     # FastAPI app
├── credentials/                    # Service account (gitignore)
├── data/                          # Tokens locales (gitignore)
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```

## Setup Local

### 1. Clonar y crear entorno

```bash
cd directToVet
python -m venv venv
source venv/bin/activate  # Linux/Mac
# o: venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

### 2. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con tus credenciales
```

### 3. Configurar Google Sheets

1. Crear proyecto en [Google Cloud Console](https://console.cloud.google.com)
2. Habilitar API de Google Sheets
3. Crear cuenta de servicio y descargar JSON
4. Guardar en `credentials/service_account.json`
5. Crear spreadsheet con las hojas: `vets`, `catalog`, `customers`, `orders`, `events`, `shipping_zones`
6. Compartir spreadsheet con el email de la cuenta de servicio

### 4. Configurar Twilio

1. Crear cuenta en [Twilio](https://console.twilio.com)
2. Activar WhatsApp Sandbox (desarrollo) o Business (producción)
3. Configurar webhook: `https://tu-dominio.com/twilio/inbound`

### 5. Configurar Mercado Pago

1. Crear app en [MP Developers](https://www.mercadopago.com.ar/developers/panel)
2. Tipo: Marketplace / Checkout Pro
3. Redirect URI: `https://tu-dominio.com/mp/oauth/callback`
4. Guardar Client ID y Secret en `.env`

### 6. Ejecutar

```bash
# Desarrollo
python -m app.main

# O con uvicorn
uvicorn app.main:app --reload --port 8000
```

### 7. Exponer localmente (para webhooks)

```bash
# Con ngrok
ngrok http 8000

# Actualizar WEBHOOK_BASE_URL en .env
```

## Google Sheets - Estructura

### Hoja: `vets`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| vet_id | string | ID único (ej: VET001) |
| name | string | Nombre de la veterinaria |
| whatsapp_e164 | string | WhatsApp (+5491155551234) |
| active | boolean | TRUE/FALSE |
| mp_connected | boolean | TRUE/FALSE |
| mp_user_id | string | ID de usuario en MP |
| created_at | datetime | Fecha de creación |
| updated_at | datetime | Última actualización |

### Hoja: `catalog`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| vet_id | string | ID de la vet (o "ALL") |
| sku | string | SKU del producto |
| ean | string | Código de barras |
| product_name | string | Nombre del producto |
| presentation | string | Presentación (ej: "15kg") |
| description | string | Descripción |
| price_distributor | number | Precio distribuidor |
| price_customer | number | Precio al cliente |
| currency | string | ARS |
| stock | number | Stock disponible |
| active | boolean | TRUE/FALSE |
| updated_at | datetime | Última actualización |

### Hoja: `customers`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| customer_id | string | ID único (ej: CUST-ABC12345) |
| vet_id | string | ID de la veterinaria |
| name | string | Nombre |
| lastname | string | Apellido |
| email | string | Email |
| whatsapp_e164 | string | WhatsApp (+5491155551234) |
| address | string | Dirección |
| pet_type | string | Tipo de mascota (Perro, Gato, etc.) |
| pet_name | string | Nombre de la mascota |
| notes | string | Notas adicionales |
| active | boolean | TRUE/FALSE |
| created_at | datetime | Fecha de creación |
| updated_at | datetime | Última actualización |

### Hoja: `orders`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| order_id | string | ID único (ej: ORD-A1B2C3D4) |
| vet_id | string | ID de la veterinaria |
| customer_name | string | Nombre del cliente |
| customer_lastname | string | Apellido |
| customer_email | string | Email |
| customer_whatsapp_e164 | string | WhatsApp del cliente |
| delivery_mode | string | PICKUP / DELIVERY |
| delivery_address | string | Dirección (si DELIVERY) |
| delivery_zone | string | Zona de envío |
| items | string | JSON con items del pedido |
| subtotal | number | Subtotal (productos) |
| shipping_cost | number | Costo de envío |
| total_amount | number | Monto total |
| currency | string | ARS |
| status | string | Estado del pedido |
| payment_method | string | MERCADO_PAGO / CASH / TRANSFER |
| mp_preference_id | string | ID de preferencia MP |
| mp_payment_id | string | ID de pago MP |
| mp_status | string | Estado del pago MP |
| external_reference | string | DTV\|VET_ID\|ORDER_ID |
| created_at | datetime | Fecha de creación |
| updated_at | datetime | Última actualización |

### Hoja: `shipping_zones`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| vet_id | string | ID de la veterinaria |
| zone_name | string | Nombre de la zona |
| shipping_cost | number | Costo de envío |
| active | boolean | TRUE/FALSE |

## Flujo de Uso

### 1. Veterinario inicia conversación
```
Vet: Hola
Bot: Hola! Soy el asistente de Direct to Vet. ¿En qué te puedo ayudar?
```

### 2. Buscar productos
```
Vet: Busco alimento para perro adulto
Bot: Encontré estos productos:
     1) Royal Canin Adult Medium 15kg - $45.000
     2) Royal Canin Adult Maxi 15kg - $48.000
     ...
```

### 3. Armar carrito
```
Vet: Agregame 2 del primero
Bot: Agregado: 2x Royal Canin Adult Medium 15kg
     Total parcial: $90.000
```

### 4. Crear pedido
```
Vet: Quiero crear el pedido para Juan Pérez, email juan@mail.com, whatsapp +5491155551234
Bot: Pedido creado: ORD-A1B2C3D4
     ¿Retira en local o envío a domicilio?
```

### 5. Generar y enviar link
```
Vet: Retira. Enviále el link de pago
Bot: Listo! Le envié el link de pago a Juan por WhatsApp.
```

## Testing

```bash
# Test del endpoint
curl -X POST http://localhost:8000/test/message \
  -H "Content-Type: application/json" \
  -d '{"vet_id": "VET001", "message": "Hola"}'

# Test de catálogo
curl "http://localhost:8000/test/catalog?query=perro"
```

## Producción

### Docker

```bash
docker build -t direct-to-vet .
docker run -p 8080:8080 --env-file .env direct-to-vet
```

### AWS App Runner

**Requisitos**: Docker, AWS CLI configurado, cuenta AWS

```bash
# Variables (reemplazar con tus valores)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
REPO_NAME=direct-to-vet

# 1. Crear repositorio ECR (una sola vez)
aws ecr create-repository --repository-name $REPO_NAME --region $REGION

# 2. Build de la imagen
docker build -t direct-to-vet .

# 3. Login a ECR y push
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
docker tag direct-to-vet:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest

# 4. Crear servicio App Runner (console o CLI)
```

**Configuración de App Runner:**
- **Source**: ECR image
- **Port**: 8080
- **Health check path**: `/health`
- **CPU**: 1 vCPU (mínimo recomendado)
- **Memory**: 2 GB (para FFmpeg + transcripción de audio)

**Variables de entorno requeridas:**
```
ENV=production
GOOGLE_API_KEY=xxx
GOOGLE_SHEETS_SPREADSHEET_ID=xxx
TWILIO_ACCOUNT_SID=xxx
TWILIO_AUTH_TOKEN=xxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+xxx
MP_CLIENT_ID=xxx
MP_CLIENT_SECRET=xxx
MP_REDIRECT_URI=https://xxx.awsapprunner.com/mp/oauth/callback
WEBHOOK_BASE_URL=https://xxx.awsapprunner.com
```

**Nota sobre Google Sheets en producción:**
Para producción, crear secret en AWS Secrets Manager con el JSON de service account y configurar `GOOGLE_SHEETS_CREDENTIALS_JSON` como variable de entorno con el contenido del secret.

### Google Cloud Run

```bash
gcloud run deploy direct-to-vet \
  --source . \
  --region southamerica-east1 \
  --allow-unauthenticated
```

## Licencia

Proyecto privado - Todos los derechos reservados.
