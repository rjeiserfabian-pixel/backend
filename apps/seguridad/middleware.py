"""Registro automático de auditoría para toda acción de escritura de la API."""
import json
import logging
import re

from .auditoria import ip_de, limpiar, registrar
from .models import Auditoria, Usuario

logger = logging.getLogger(__name__)

METODOS_DE_ESCRITURA = {'POST', 'PUT', 'PATCH', 'DELETE'}
MAX_CUERPO = 20 * 1024  # solo se guardan cuerpos JSON pequeños

# Rutas de escritura sin valor de auditoría (ruido) o públicas de alto volumen.
RUTAS_IGNORADAS = {
    '/api/seguridad/token/refresh/',
    '/api/avisos/generar/',
    '/api/ventas/transacciones/kiosko/generar-ticket/',
}
RUTA_LOGIN = '/api/seguridad/login/'
RUTA_LOGOUT = '/api/seguridad/logout/'

_ID = re.compile(r'^(\d+|[0-9a-fA-F-]{32,36})$')


def _ruta_a_partes(path):
    """/api/ventas/transacciones/12/anular/ -> (modulo, recurso, id, subaccion)."""
    partes = [p for p in path.split('/') if p][1:]  # sin "api"
    modulo = partes[0] if partes else 'api'
    recurso = partes[1] if len(partes) > 1 else modulo
    registro_id, subaccion = None, None
    for parte in partes[2:]:
        if registro_id is None and _ID.match(parte):
            registro_id = parte
        elif subaccion is None:
            subaccion = parte
    return modulo, recurso, registro_id, subaccion


def _accion(metodo, registro_id, subaccion):
    if subaccion:
        return subaccion.replace('-', '_').upper()
    if metodo == 'DELETE':
        return 'ELIMINAR'
    if metodo == 'POST':
        return 'CREAR'
    return 'EDITAR'


class AuditoriaMiddleware:
    """
    Guarda en `Auditoria` cada escritura exitosa (y los accesos denegados a escrituras).
    Las vistas que usan `auditoria.registrar()` ya dejan su propio registro detallado y
    aquí se omiten. El usuario lo aporta JWTAuthenticationAuditable.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cuerpo = self._leer_cuerpo(request)
        respuesta = self.get_response(request)
        if request.method in METODOS_DE_ESCRITURA and request.path.startswith('/api/'):
            try:
                self._registrar(request, respuesta, cuerpo)
            except Exception:  # nunca interrumpir la respuesta por la auditoría
                logger.exception('Falló la auditoría automática de %s %s', request.method, request.path)
        return respuesta

    @staticmethod
    def _leer_cuerpo(request):
        """Cuerpo JSON saneado (sin contraseñas ni tokens); None si no aplica."""
        if request.method not in METODOS_DE_ESCRITURA or not request.path.startswith('/api/'):
            return None
        if not (request.content_type or '').startswith('application/json'):
            return {'_tipo': 'archivo/formulario'} if request.content_type else None
        try:
            if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_CUERPO:
                return {'_omitido': 'cuerpo muy grande'}
            return limpiar(json.loads(request.body or b'{}'))
        except (ValueError, TypeError):
            return None

    def _registrar(self, request, respuesta, cuerpo):
        ruta = request.path
        if ruta in RUTAS_IGNORADAS or getattr(request, '_auditoria_registrada', False):
            return
        usuario = getattr(request, 'usuario_auditoria', None)
        modulo, recurso, registro_id, subaccion = _ruta_a_partes(ruta)
        estado = respuesta.status_code

        if ruta == RUTA_LOGIN:
            nombre = (cuerpo or {}).get('username') if isinstance(cuerpo, dict) else None
            if estado == 200:
                accion, usuario = 'LOGIN', Usuario.objects.filter(username=nombre).first() if nombre else None
            elif estado in (400, 401):
                accion = 'LOGIN_FALLIDO'
            else:
                return
            Auditoria.objects.create(
                id_usuario=usuario, modulo='SEGURIDAD', accion=accion, tabla_afectada='login',
                datos_nuevos={'username': nombre}, ip=ip_de(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
            )
            return

        if estado == 403:
            accion = 'ACCESO_DENEGADO'
        elif 200 <= estado < 300:
            accion = 'LOGOUT' if ruta == RUTA_LOGOUT else _accion(request.method, registro_id, subaccion)
        else:
            return  # errores de validación y otros: no son acciones realizadas

        if registro_id is None and request.method == 'POST' and estado == 201:
            registro_id = self._id_creado(respuesta)

        Auditoria.objects.create(
            id_usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
            modulo=modulo.upper()[:50], accion=accion[:50], tabla_afectada=recurso[:50],
            registro_id=str(registro_id)[:50] if registro_id else None,
            datos_nuevos=({'ruta': ruta, 'datos': cuerpo} if cuerpo else {'ruta': ruta}),
            ip=ip_de(request), user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
        )

    @staticmethod
    def _id_creado(respuesta):
        """Id del registro recién creado, leído de la respuesta JSON (si es pequeña)."""
        try:
            if 'json' not in (respuesta.get('Content-Type') or '') or len(respuesta.content) > MAX_CUERPO:
                return None
            datos = json.loads(respuesta.content)
            datos = datos.get('data', datos) if isinstance(datos, dict) else None
            return datos.get('id') if isinstance(datos, dict) else None
        except (ValueError, AttributeError):
            return None
