"""
config.py
Configuración centralizada usando Pydantic Settings.
Lee variables de entorno y provee defaults seguros.
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===========================================
    # Environment
    # ===========================================
    env: str = "development"
    debug: bool = False

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    # ===========================================
    # Google AI (Gemini)
    # ===========================================
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # ===========================================
    # Google Sheets
    # ===========================================
    google_sheets_credentials_path: str = "./credentials/service_account.json"
    google_sheets_credentials_json: Optional[str] = None  # JSON string for container deployments
    google_sheets_spreadsheet_id: str = ""

    # Sheet names
    sheet_vets: str = "vets"
    sheet_catalog: str = "catalog"
    sheet_orders: str = "orders"
    sheet_events: str = "events"
    sheet_customers: str = "customers"
    sheet_shipping: str = "costo_envio"

    # ===========================================
    # Twilio (WhatsApp)
    # ===========================================
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""
    twilio_payment_template_sid: str = ""  # Content SID para template de pago (HX...)

    # ===========================================
    # Mercado Pago OAuth
    # ===========================================
    mp_client_id: str = ""
    mp_client_secret: str = ""
    mp_redirect_uri: str = ""

    # MP API URLs
    mp_auth_url: str = "https://auth.mercadopago.com.ar/authorization"
    mp_token_url: str = "https://api.mercadopago.com/oauth/token"
    mp_api_base_url: str = "https://api.mercadopago.com"

    # ===========================================
    # Webhooks
    # ===========================================
    webhook_base_url: str = ""
    webhook_secret: str = ""

    # ===========================================
    # Email (SendGrid)
    # ===========================================
    sendgrid_api_key: Optional[str] = None
    ops_email: str = ""
    from_email: str = "noreply@directtovet.com"

    # ===========================================
    # Token Storage
    # ===========================================
    local_token_store_path: str = "./data/tokens.json"

    # ===========================================
    # Google Cloud (prod)
    # ===========================================
    gcp_project_id: Optional[str] = None

    # ===========================================
    # Helpers
    # ===========================================

    def get_mp_oauth_url(self, state: str) -> str:
        """Genera URL de autorización OAuth de MP."""
        params = {
            "client_id": self.mp_client_id,
            "response_type": "code",
            "platform_id": "mp",
            "redirect_uri": self.mp_redirect_uri,
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.mp_auth_url}?{query}"

    def has_sendgrid(self) -> bool:
        """Verifica si SendGrid está configurado."""
        return bool(self.sendgrid_api_key and self.ops_email)

    def has_twilio(self) -> bool:
        """Verifica si Twilio está configurado."""
        return bool(
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_whatsapp_number
        )

    def has_mp(self) -> bool:
        """Verifica si Mercado Pago está configurado."""
        return bool(self.mp_client_id and self.mp_client_secret)


@lru_cache
def get_settings() -> Settings:
    """Singleton para configuración."""
    return Settings()
