import logging
import requests
from typing import Dict, Optional
from django.conf import settings

logger = logging.getLogger(__name__)

class ConsultaVehicularService:
    """
    Servicio para consultar datos de vehículos mediante la API de yupay.dev
    """
    # Si yupay.dev tiene subdominio api, ajustarlo si falla, pero el endpoint de la imagen es /v1/plate/{placa}
    BASE_URL = "https://api.yupay.dev/v1/plate"

    def __init__(self, token: Optional[str] = None):
        # Preferir token inyectado o por defecto el provisto en el plan
        self.token = token or getattr(settings, 'YUPAY_TOKEN', 'ypk_rwXZFUlsbDVsYrSzTviYIZfLUdIuahvxWjDpOsoDIZaAUXxukUaiYNnQgZhMcYls')
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
        }

    def consultar_placa(self, placa: str) -> Dict:
        """
        Consulta datos del vehículo por número de placa en yupay.dev.
        Retorna un diccionario con los datos normalizados.
        """
        url = f"{self.BASE_URL}/{placa}"
        try:
            # 3.2 Tareas pesadas y llamadas externas: timeout explícito
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # yupay.dev retorna directamente el objeto
            return {
                'placa': data.get('placa', placa),
                'marca': data.get('marca', ''),
                'modelo': data.get('modelo', ''),
                'clase': data.get('clase', ''),
                'tipo': data.get('tipo', ''),
                'uso': data.get('uso', ''),
                'anio_fabricacion': data.get('anioFabricacion', None),
                'numero_asientos': data.get('numAsientos', None),
                'numero_motor': data.get('numMotor', ''),
                'numero_serie': data.get('numSerie', '')
            }

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 401:
                raise ValueError("[Yupay] Token inválido o expirado.")
            elif status == 404:
                raise ValueError(f"[Yupay] Placa '{placa}' no encontrada.")
            elif status == 429:
                raise ValueError("[Yupay] Límite de consultas excedido.")
            else:
                try:
                    error_data = e.response.json()
                    msg = error_data.get('mensaje', str(e))
                except Exception:
                    msg = str(e)
                raise ValueError(f"[Yupay] Error HTTP {status}: {msg}")
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[Yupay] Error de conexión: {e}")
