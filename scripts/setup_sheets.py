"""
setup_sheets.py
Script para crear las hojas y datos de prueba en Google Sheets.
"""

import sys
from pathlib import Path

# Agregar el directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from app.config import get_settings

settings = get_settings()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_client():
    """Obtiene cliente de Google Sheets."""
    creds = Credentials.from_service_account_file(
        settings.google_sheets_credentials_path,
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def setup_sheets():
    """Crea las hojas y datos de prueba."""
    print("Conectando a Google Sheets...")
    client = get_client()
    spreadsheet = client.open_by_key(settings.google_sheets_spreadsheet_id)

    print(f"Spreadsheet: {spreadsheet.title}")

    # Headers para cada hoja
    sheets_config = {
        "vets": [
            "vet_id", "name", "whatsapp_e164", "active", "mp_connected",
            "mp_user_id", "created_at", "updated_at"
        ],
        "catalog": [
            "vet_id", "sku", "ean", "product_name", "presentation",
            "description", "price_distributor", "price_customer",
            "currency", "stock", "active", "updated_at"
        ],
        "orders": [
            "order_id", "vet_id", "customer_name", "customer_lastname",
            "customer_email", "customer_whatsapp_e164", "delivery_mode",
            "delivery_address", "items_json", "total_amount", "currency",
            "status", "mp_preference_id", "mp_payment_id", "mp_status",
            "external_reference", "created_at", "updated_at"
        ],
        "events": [
            "event_id", "order_id", "vet_id", "type", "payload_json", "created_at"
        ],
    }

    # Crear o actualizar cada hoja
    existing_sheets = [ws.title for ws in spreadsheet.worksheets()]

    for sheet_name, headers in sheets_config.items():
        print(f"\nConfigurando hoja: {sheet_name}")

        if sheet_name in existing_sheets:
            print(f"  - Hoja existe, actualizando headers...")
            worksheet = spreadsheet.worksheet(sheet_name)
            worksheet.update('A1', [headers])
        else:
            print(f"  - Creando hoja nueva...")
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=len(headers)
            )
            worksheet.update('A1', [headers])

        # Formatear headers (negrita)
        worksheet.format('A1:Z1', {
            'textFormat': {'bold': True},
            'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
        })

        print(f"  - Headers: {len(headers)} columnas")

    # Eliminar Sheet1 default si existe
    if "Sheet1" in existing_sheets or "Hoja 1" in existing_sheets:
        try:
            default_sheet = spreadsheet.worksheet("Sheet1")
            spreadsheet.del_worksheet(default_sheet)
            print("\nEliminada hoja 'Sheet1' por defecto")
        except:
            try:
                default_sheet = spreadsheet.worksheet("Hoja 1")
                spreadsheet.del_worksheet(default_sheet)
                print("\nEliminada hoja 'Hoja 1' por defecto")
            except:
                pass

    print("\n" + "="*50)
    print("Hojas creadas exitosamente!")
    print("="*50)

    # Preguntar si agregar datos de prueba
    add_test = input("\n¿Agregar datos de prueba? (s/n): ").lower().strip()

    if add_test == 's':
        add_test_data(spreadsheet)

    print("\n¡Setup completado!")
    print(f"Spreadsheet URL: https://docs.google.com/spreadsheets/d/{settings.google_sheets_spreadsheet_id}")


def add_test_data(spreadsheet):
    """Agrega datos de prueba."""
    now = datetime.utcnow().isoformat()

    # Datos de prueba para vets
    print("\nAgregando veterinaria de prueba...")
    vets_ws = spreadsheet.worksheet("vets")
    vets_data = [
        ["VET001", "Veterinaria San Martín", "+5491155551234", "TRUE", "FALSE", "", now, now],
        ["VET002", "Pet Shop Centro", "+5491166662345", "TRUE", "FALSE", "", now, now],
    ]
    vets_ws.append_rows(vets_data, value_input_option="USER_ENTERED")
    print(f"  - Agregadas {len(vets_data)} veterinarias")

    # Datos de prueba para catalog
    print("\nAgregando productos de prueba...")
    catalog_ws = spreadsheet.worksheet("catalog")
    catalog_data = [
        ["ALL", "RC-AM-15", "7896181200001", "Royal Canin Adult Medium", "15kg",
         "Alimento balanceado para perros adultos de razas medianas",
         "35000", "45000", "ARS", "20", "TRUE", now],
        ["ALL", "RC-AX-15", "7896181200002", "Royal Canin Adult Maxi", "15kg",
         "Alimento balanceado para perros adultos de razas grandes",
         "38000", "48000", "ARS", "15", "TRUE", now],
        ["ALL", "RC-AM-3", "7896181200003", "Royal Canin Adult Medium", "3kg",
         "Alimento balanceado para perros adultos de razas medianas",
         "12000", "15000", "ARS", "30", "TRUE", now],
        ["ALL", "RC-PUP-3", "7896181200004", "Royal Canin Puppy Medium", "3kg",
         "Alimento para cachorros de razas medianas hasta 12 meses",
         "13000", "16500", "ARS", "25", "TRUE", now],
        ["ALL", "RC-CAT-2", "7896181200005", "Royal Canin Indoor Cat", "2kg",
         "Alimento para gatos adultos de interior",
         "15000", "19000", "ARS", "18", "TRUE", now],
        ["ALL", "RC-CAT-4", "7896181200006", "Royal Canin Indoor Cat", "4kg",
         "Alimento para gatos adultos de interior",
         "28000", "35000", "ARS", "12", "TRUE", now],
        ["ALL", "RC-MINI-3", "7896181200007", "Royal Canin Mini Adult", "3kg",
         "Alimento para perros adultos de razas pequeñas",
         "14000", "17500", "ARS", "22", "TRUE", now],
        ["ALL", "RC-SENS-2", "7896181200008", "Royal Canin Sensible", "2kg",
         "Alimento para gatos con sensibilidad digestiva",
         "16000", "20000", "ARS", "10", "TRUE", now],
    ]
    catalog_ws.append_rows(catalog_data, value_input_option="USER_ENTERED")
    print(f"  - Agregados {len(catalog_data)} productos")

    print("\nDatos de prueba agregados!")
    print("\nVeterinarias de prueba:")
    print("  - VET001: Veterinaria San Martín (+5491155551234)")
    print("  - VET002: Pet Shop Centro (+5491166662345)")


if __name__ == "__main__":
    setup_sheets()
