import logging
import requests
from typing import Dict, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


class ConsultaVehicularSecundariaService:
    """
    Servicio complementario para consultar datos de vehículos en json.pe.
    Yupay no devuelve el color del vehículo; json.pe sí. Se usa exclusivamente
    para completar ese dato (y como respaldo de marca/modelo/serie/motor si
    Yupay no los trajo) — nunca como fuente principal.

    Ojo: json.pe (docs.json.pe) es un proveedor totalmente distinto a
    json.com.pe (cuyo endpoint documentado no pudo verificarse). No confundir.
    """
    BASE_URL = "https://api.json.pe/api/placa"

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(settings, 'JSON_PE_TOKEN', None)
        if not self.token:
            logger.warning("JSON_PE_TOKEN no configurado; las consultas a json.pe fallarán.")
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def consultar_placa(self, placa: str) -> Dict:
        """
        Consulta datos del vehículo por número de placa en json.pe.
        Retorna el objeto 'data' de la respuesta, normalizado a los nombres
        de campo que ya usa el resto del sistema (numero_serie en vez de
        serie/vin, numero_motor en vez de motor).
        """
        try:
            response = requests.post(
                self.BASE_URL,
                json={'placa': placa},
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            data = body.get('data') or {}

            return {
                'marca': data.get('marca', ''),
                'modelo': data.get('modelo', ''),
                'color': data.get('color', ''),
                'numero_motor': data.get('motor', ''),
                # json.pe no distingue serie de VIN (para la mayoría de
                # vehículos son el mismo dato); se usa 'serie' y se cae a
                # 'vin' si no viniera.
                'numero_serie': data.get('serie') or data.get('vin', ''),
                # No documentado oficialmente por json.pe, pero confirmado en
                # producción que la respuesta real sí trae 'anio' (a veces
                # vacío) — se usa solo como respaldo si Yupay no lo trae.
                'anio_fabricacion': data.get('anio') or None,
            }

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            # json.pe reutiliza 401 tanto para token inválido como para plan
            # vencido/sin créditos (mensajes muy distintos) — se muestra el
            # mensaje real que envía la API en vez de asumir siempre "token
            # inválido", para no diagnosticar mal la causa real del fallo.
            try:
                mensaje_api = (e.response.json() or {}).get('message') if e.response is not None else None
            except Exception:
                mensaje_api = None

            if status == 404:
                raise ValueError(f"[json.pe] Placa '{placa}' no encontrada.")
            elif status == 429:
                raise ValueError(mensaje_api or "[json.pe] Límite de consultas excedido.")
            elif status == 401:
                raise ValueError(f"[json.pe] {mensaje_api or 'Token inválido o expirado.'}")
            else:
                raise ValueError(f"[json.pe] Error HTTP {status}: {mensaje_api or str(e)}")
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[json.pe] Error de conexión: {e}")


class ConsultaVehicularService:
    """
    Servicio para consultar datos de vehículos mediante la API de yupay.dev
    (proveedor principal), complementado con json.pe (proveedor secundario,
    solo para "color" y como respaldo de marca/modelo/serie/motor).

    El resto del sistema (kiosko, taller, consulta pública) sigue llamando
    únicamente a consultar_placa(): no necesitan saber que hay dos proveedores.
    """
    # Si yupay.dev tiene subdominio api, ajustarlo si falla, pero el endpoint de la imagen es /v1/plate/{placa}
    BASE_URL = "https://api.yupay.dev/v1/plate"

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(settings, 'YUPAY_TOKEN', None)
        if not self.token:
            logger.warning("YUPAY_TOKEN no configurado; las consultas a Yupay.dev fallarán.")
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
        }

    def _consultar_yupay(self, placa: str) -> Dict:
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

    def consultar_placa(self, placa: str) -> Dict:
        """
        Consulta datos del vehículo por número de placa.
        Yupay es la fuente principal (año, clase, tipo, uso, asientos, etc.).
        Si responde, se complementa —sin poder romper la respuesta— con
        json.pe para obtener el color y respaldar marca/modelo/serie/motor.
        Si Yupay falla pero json.pe responde, se devuelve lo que json.pe
        tenga (mejor datos parciales que nada); si ambos fallan, se propaga
        el error de Yupay tal cual se hacía antes de esta integración.
        """
        try:
            datos = self._consultar_yupay(placa)
        except (ValueError, ConnectionError) as error_yupay:
            try:
                datos_secundarios = ConsultaVehicularSecundariaService().consultar_placa(placa)
            except Exception:
                raise error_yupay
            return {
                'placa': placa,
                'marca': datos_secundarios.get('marca', ''),
                'modelo': datos_secundarios.get('modelo', ''),
                'clase': '',
                'tipo': '',
                'uso': '',
                'anio_fabricacion': datos_secundarios.get('anio_fabricacion'),
                'numero_asientos': None,
                'numero_motor': datos_secundarios.get('numero_motor', ''),
                'numero_serie': datos_secundarios.get('numero_serie', ''),
                'color': datos_secundarios.get('color', ''),
            }

        # Yupay respondió: se intenta completar con json.pe, pero una falla
        # aquí (API caída, token vencido, placa no encontrada en json.pe,
        # etc.) NUNCA debe afectar la respuesta principal ya obtenida.
        try:
            datos_secundarios = ConsultaVehicularSecundariaService().consultar_placa(placa)
            datos['color'] = datos_secundarios.get('color') or None
            if not datos.get('marca'):
                datos['marca'] = datos_secundarios.get('marca', '')
            if not datos.get('modelo'):
                datos['modelo'] = datos_secundarios.get('modelo', '')
            if not datos.get('numero_motor'):
                datos['numero_motor'] = datos_secundarios.get('numero_motor', '')
            if not datos.get('numero_serie'):
                datos['numero_serie'] = datos_secundarios.get('numero_serie', '')
            if not datos.get('anio_fabricacion'):
                datos['anio_fabricacion'] = datos_secundarios.get('anio_fabricacion')
        except Exception as error_secundario:
            logger.warning(f"[json.pe] No se pudo completar el color para placa {placa}: {error_secundario}")
            datos['color'] = None

        return datos
