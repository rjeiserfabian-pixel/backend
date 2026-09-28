import logging
from typing import Optional

import requests
from django.conf import settings

from .exceptions import ApiSunatError

logger = logging.getLogger(__name__)


class ApiSunatClient:
    """
    Cliente HTTP hacia la API SUNAT propia del taller (ver documentación
    'API_SUNAT_Mejorado.pdf'). Cada método corresponde a un endpoint
    documentado; ninguno se llama automáticamente — siempre los dispara una
    acción manual del usuario a través de FacturacionService.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: int = 30):
        self.base_url = (base_url or getattr(settings, 'API_SUNAT_BASE_URL', 'http://localhost/API_SUNAT')).rstrip('/')
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = requests.post(
                url, json=payload, timeout=self.timeout,
                headers={'Content-Type': 'application/json'},
            )
        except requests.exceptions.RequestException as exc:
            logger.error("Error de conexión con la API SUNAT [%s]: %s", url, exc)
            raise ApiSunatError(f"No se pudo conectar con la API SUNAT ({url}): {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("Respuesta no-JSON de la API SUNAT [%s] (HTTP %s): %s", url, response.status_code, response.text[:500])
            raise ApiSunatError(
                f"La API SUNAT respondió con un cuerpo no interpretable (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 500:
            logger.error("Error de servidor en la API SUNAT [%s] (HTTP %s): %s", url, response.status_code, data)
            raise ApiSunatError(f"Error del servidor de la API SUNAT (HTTP {response.status_code}).")

        return data

    def enviar_comprobante(self, payload: dict) -> dict:
        """Factura (01), Boleta (03), Nota de Crédito (07) y Nota de Débito (08)."""
        return self._post('post.php', payload)

    def enviar_guia_remision(self, payload: dict) -> dict:
        """Guía de Remisión Remitente (09), vía la API GRE directa de SUNAT."""
        return self._post('post.php', payload)

    def solicitar_baja(self, payload: dict) -> dict:
        """Comunicación de Baja (anulación); la respuesta trae un ticket a consultar despues."""
        return self._post('baja.php', payload)

    def consultar_ticket(self, payload: dict) -> dict:
        return self._post('resumen_ticket.php', payload)
