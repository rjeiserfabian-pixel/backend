import logging
import requests
from typing import Dict, List, Tuple, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


# ==============================================================================
# CLASE BASE
# ==============================================================================

class BaseConsultaProvider:
    """
    Clase base abstracta que define la interfaz estándar para todos los
    proveedores de consulta de DNI y RUC. Cada proveedor debe devolver datos
    en el mismo formato estandarizado para que el orquestador sea transparente.
    """
    nombre_proveedor = "BaseProvider"

    def consultar_dni(self, dni: str) -> Dict:
        raise NotImplementedError(
            f"{self.__class__.__name__} debe implementar 'consultar_dni'"
        )

    def consultar_ruc(self, ruc: str) -> Dict:
        raise NotImplementedError(
            f"{self.__class__.__name__} debe implementar 'consultar_ruc'"
        )

    def _raise_from_http_error(self, e: requests.exceptions.HTTPError, doc: str):
        """Convierte errores HTTP en mensajes de usuario claros."""
        status = e.response.status_code if e.response is not None else None
        if status == 401:
            raise ValueError(f"[{self.nombre_proveedor}] Token inválido o expirado.")
        elif status == 404:
            raise ValueError(f"[{self.nombre_proveedor}] Documento '{doc}' no encontrado.")
        elif status == 429:
            raise ValueError(f"[{self.nombre_proveedor}] Límite de consultas excedido.")
        else:
            try:
                error_data = e.response.json()
                msg = error_data.get('mensaje', error_data.get('error', str(e)))
            except Exception:
                msg = str(e)
            raise ValueError(f"[{self.nombre_proveedor}] Error HTTP {status}: {msg}")


# ==============================================================================
# PROVEEDOR PRINCIPAL: DeColecta / APIs.net.pe
# ==============================================================================

class DeColectaProvider(BaseConsultaProvider):
    """
    Proveedor Principal: API de DeColecta (APIs.net.pe).
    Autenticación: Bearer Token en el header Authorization.
    Configuración: settings.TOKENPERU_TOKEN
    """
    nombre_proveedor = "DeColecta (Principal)"
    BASE_URL = "https://api.decolecta.com/v1"

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(settings, 'TOKENPERU_TOKEN', None)
        if not self.token:
            raise ValueError(
                "Token de DeColecta no configurado. "
                "Agregue TOKENPERU_TOKEN en settings.py"
            )
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
            'Referer': 'https://apis.net.pe',
        }

    def consultar_dni(self, dni: str) -> Dict:
        """Consulta datos de persona natural por DNI (RENIEC)."""
        url = f"{self.BASE_URL}/reniec/dni"
        try:
            response = requests.get(
                url, headers=self.headers, params={'numero': dni}, timeout=10
            )
            response.raise_for_status()
            result = response.json()

            paterno = result.get('first_last_name', result.get('apellidoPaterno', ''))
            materno = result.get('second_last_name', result.get('apellidoMaterno', ''))
            nombres = result.get('first_name', result.get('nombres', ''))

            return {
                'dni': result.get('document_number', result.get('numeroDocumento', dni)),
                'nombres': nombres,
                'apellido_paterno': paterno,
                'apellido_materno': materno,
                'nombre_completo': result.get(
                    'full_name', f"{paterno} {materno} {nombres}".strip()
                ),
                'codigo_verificacion': result.get(
                    'codigoVerificacion', result.get('verification_code', '')
                ),
                'fecha_nacimiento': result.get('fechaNacimiento', result.get('birth_date', '')),
                'sexo': result.get('sexo', result.get('gender', '')),
                'ubigeo': result.get('ubigeo', result.get('ubigeo_code', '')),
                'direccion': result.get('direccion', result.get('address', '')),
            }
        except requests.exceptions.HTTPError as e:
            self._raise_from_http_error(e, dni)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[{self.nombre_proveedor}] Error de conexión: {e}")

    def consultar_ruc(self, ruc: str) -> Dict:
        """Consulta datos de empresa/persona jurídica por RUC (SUNAT)."""
        url = f"{self.BASE_URL}/sunat/ruc"
        try:
            response = requests.get(
                url, headers=self.headers, params={'numero': ruc}, timeout=10
            )
            response.raise_for_status()
            result = response.json()

            return {
                'ruc': result.get('numero_documento', result.get('numeroDocumento', ruc)),
                'razon_social': result.get('razon_social', result.get('nombre', '')),
                'nombre_comercial': result.get(
                    'nombre_comercial', result.get('nombreComercial', '')
                ),
                'tipo_contribuyente': result.get(
                    'tipo_contribuyente', result.get('tipoContribuyente', '')
                ),
                'estado': result.get('estado', ''),
                'condicion': result.get('condicion', ''),
                'direccion': result.get('direccion', ''),
                'departamento': result.get('departamento', ''),
                'provincia': result.get('provincia', ''),
                'distrito': result.get('distrito', ''),
                'ubigeo': result.get('ubigeo', ''),
                'fecha_inscripcion': result.get(
                    'fecha_inscripcion', result.get('fechaInscripcion', '')
                ),
                'actividad_economica': result.get('actividad_economica', []),
                'via_tipo': result.get('via_tipo', result.get('viaTipo', '')),
                'via_nombre': result.get('via_nombre', result.get('viaNombre', '')),
                'numero': result.get('numero', ''),
                'interior': result.get('interior', ''),
                'lote': result.get('lote', ''),
                'departamento_dir': result.get('dpto', ''),
                'manzana': result.get('manzana', ''),
                'kilometro': result.get('kilometro', ''),
                'es_agente_retencion': result.get('es_agente_retencion', False),
                'es_buen_contribuyente': result.get('es_buen_contribuyente', False),
            }
        except requests.exceptions.HTTPError as e:
            self._raise_from_http_error(e, ruc)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[{self.nombre_proveedor}] Error de conexión: {e}")


# ==============================================================================
# PROVEEDOR SECUNDARIO: APIsPERU (Fallback)
# ==============================================================================

class APIsPeruProvider(BaseConsultaProvider):
    """
    Proveedor de Respaldo: APIsPERU (dniruc.apisperu.com).
    Autenticación: JWT como query parameter '?token=' (y también como Bearer Header).
    Configuración: settings.APISPERU_TOKEN
    """
    nombre_proveedor = "APIsPERU (Fallback)"
    BASE_URL = "https://dniruc.apisperu.com/api/v1"

    def __init__(self, token: Optional[str] = None):
        self.token = token or getattr(settings, 'APISPERU_TOKEN', None)
        if not self.token:
            raise ValueError(
                "Token de APIsPERU no configurado. "
                "Agregue APISPERU_TOKEN en settings.py"
            )
        # APIsPERU soporta el token tanto por query param como por header Bearer
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
        }

    def consultar_dni(self, dni: str) -> Dict:
        """Consulta datos de persona natural por DNI (RENIEC vía APIsPERU)."""
        url = f"{self.BASE_URL}/dni/{dni}"
        # APIsPERU acepta el token como query param adicionalmente al header
        params = {'token': self.token}
        try:
            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()
            result = response.json()

            # La respuesta puede venir directamente o anidada en 'data'
            data = result.get('data', result) if isinstance(result, dict) else result

            paterno = data.get('apellidoPaterno', '')
            materno = data.get('apellidoMaterno', '')
            nombres = data.get('nombres', '')

            return {
                'dni': data.get('dni', dni),
                'nombres': nombres,
                'apellido_paterno': paterno,
                'apellido_materno': materno,
                'nombre_completo': data.get(
                    'nombreCompleto', f"{paterno} {materno} {nombres}".strip()
                ),
                'codigo_verificacion': data.get('codVerifica', ''),
                'fecha_nacimiento': '',
                'sexo': '',
                'ubigeo': '',
                'direccion': data.get('direccion', ''),
            }
        except requests.exceptions.HTTPError as e:
            self._raise_from_http_error(e, dni)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[{self.nombre_proveedor}] Error de conexión: {e}")

    def consultar_ruc(self, ruc: str) -> Dict:
        """Consulta datos de empresa por RUC (SUNAT vía APIsPERU)."""
        url = f"{self.BASE_URL}/ruc/{ruc}"
        params = {'token': self.token}
        try:
            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()
            result = response.json()

            data = result.get('data', result) if isinstance(result, dict) else result

            return {
                'ruc': data.get('ruc', ruc),
                'razon_social': data.get('razonSocial', data.get('nombre', '')),
                'nombre_comercial': data.get('nombreComercial', ''),
                'tipo_contribuyente': '',
                'estado': data.get('estado', ''),
                'condicion': data.get('condicion', ''),
                'direccion': data.get('direccion', ''),
                'departamento': data.get('departamento', ''),
                'provincia': data.get('provincia', ''),
                'distrito': data.get('distrito', ''),
                'ubigeo': data.get('ubigeo', ''),
                'fecha_inscripcion': '',
                'actividad_economica': [],
                'via_tipo': '',
                'via_nombre': '',
                'numero': '',
                'interior': '',
                'lote': '',
                'departamento_dir': '',
                'manzana': '',
                'kilometro': '',
                'es_agente_retencion': False,
                'es_buen_contribuyente': False,
            }
        except requests.exceptions.HTTPError as e:
            self._raise_from_http_error(e, ruc)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"[{self.nombre_proveedor}] Error de conexión: {e}")


# ==============================================================================
# ORQUESTADOR CON FAILOVER AUTOMÁTICO
# ==============================================================================

class ConsultaOrchestrator:
    """
    Orquestador inteligente que gestiona múltiples proveedores de consulta.

    Estrategia de Failover:
      1. Intenta la consulta con el PROVEEDOR PRINCIPAL (DeColecta).
      2. Si falla por cualquier motivo (timeout, límite de cuota, error HTTP,
         token expirado, error de red), registra el error en el log del sistema
         y automáticamente INTENTA EL SIGUIENTE PROVEEDOR (APIsPERU).
      3. Si todos los proveedores fallan, lanza una excepción con el resumen
         de todos los errores encontrados.

    Uso:
        orchestrator = ConsultaOrchestrator()
        resultado_dni = orchestrator.consultar_dni("45678912")
        resultado_ruc = orchestrator.consultar_ruc("20601234567")
    """

    def __init__(self):
        self.providers: List[Tuple[str, BaseConsultaProvider]] = []
        self._inicializar_proveedores()

    def _inicializar_proveedores(self):
        """
        Inicializa los proveedores disponibles en orden de prioridad.
        Los proveedores cuyo token no está configurado son omitidos con una advertencia.
        """
        # Proveedor 1: DeColecta (Principal)
        if getattr(settings, 'TOKENPERU_TOKEN', None):
            try:
                self.providers.append(
                    (DeColectaProvider.nombre_proveedor, DeColectaProvider())
                )
                logger.info("✅ Proveedor DeColecta (Principal) cargado correctamente.")
            except Exception as e:
                logger.warning(f"⚠️ No se pudo cargar DeColecta: {e}")
        else:
            logger.warning(
                "⚠️ TOKENPERU_TOKEN no está configurado. "
                "DeColecta omitido como proveedor principal."
            )

        # Proveedor 2: APIsPERU (Fallback)
        if getattr(settings, 'APISPERU_TOKEN', None):
            try:
                self.providers.append(
                    (APIsPeruProvider.nombre_proveedor, APIsPeruProvider())
                )
                logger.info("✅ Proveedor APIsPERU (Fallback) cargado correctamente.")
            except Exception as e:
                logger.warning(f"⚠️ No se pudo cargar APIsPERU: {e}")
        else:
            logger.warning(
                "⚠️ APISPERU_TOKEN no está configurado. "
                "APIsPERU omitido como proveedor de respaldo."
            )

    def _ejecutar_con_failover(self, operacion: str, numero: str) -> Dict:
        """
        Método genérico que ejecuta una consulta con failover automático.

        Args:
            operacion: 'consultar_dni' o 'consultar_ruc'
            numero: El número de documento a consultar

        Returns:
            Dict con los datos estandarizados de la consulta

        Raises:
            ValueError: Si todos los proveedores fallaron
        """
        if not self.providers:
            raise ValueError(
                "No hay proveedores de consulta disponibles. "
                "Verifique que TOKENPERU_TOKEN y/o APISPERU_TOKEN estén "
                "configurados en settings.py"
            )

        errores_registrados = []

        for nombre, provider in self.providers:
            try:
                logger.info(f"🔍 Consultando '{numero}' con {nombre}...")
                metodo = getattr(provider, operacion)
                resultado = metodo(numero)
                logger.info(f"✅ Consulta exitosa con {nombre} para documento '{numero}'.")
                return resultado

            except (ValueError, ConnectionError) as e:
                # Error esperado: se hace failover al siguiente proveedor
                msg_error = str(e)
                errores_registrados.append(f"{nombre}: {msg_error}")
                logger.warning(
                    f"⚡ Fallo en {nombre} para documento '{numero}'. "
                    f"Motivo: {msg_error}. "
                    f"Activando proveedor de respaldo..."
                )
                continue

            except Exception as e:
                # Error inesperado: también se intenta con el siguiente proveedor
                msg_error = str(e)
                errores_registrados.append(f"{nombre}: Error inesperado - {msg_error}")
                logger.error(
                    f"❌ Error inesperado en {nombre} para documento '{numero}': {msg_error}",
                    exc_info=True
                )
                continue

        # Si llegamos aquí, todos los proveedores fallaron
        detalle_errores = " | ".join(errores_registrados)
        raise ValueError(
            f"Todos los proveedores de consulta fallaron para '{numero}'. "
            f"Detalles: {detalle_errores}"
        )

    def consultar_dni(self, dni: str) -> Dict:
        """Consulta DNI con failover automático entre proveedores."""
        return self._ejecutar_con_failover('consultar_dni', dni)

    def consultar_ruc(self, ruc: str) -> Dict:
        """Consulta RUC con failover automático entre proveedores."""
        return self._ejecutar_con_failover('consultar_ruc', ruc)
