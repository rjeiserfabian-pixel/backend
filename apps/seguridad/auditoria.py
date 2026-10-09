"""
Auditoría: quién hizo qué, cuándo y desde dónde.

Dos niveles que conviven:
  1. AutomaticoMiddleware (middleware.py): registra TODA acción de escritura exitosa de la API.
  2. `registrar()` (este archivo): registro detallado con el valor ANTES y DESPUÉS para lo más
     sensible (precios, stock, permisos, anulaciones, cierres de caja). Si una vista lo llama,
     el registro automático de esa misma petición se omite para no duplicar.

Registrar nunca debe romper la operación del usuario: cualquier error se escribe en el log y se ignora.
"""
import json
import logging
from decimal import Decimal, InvalidOperation

from django.core.serializers.json import DjangoJSONEncoder

from .models import Auditoria

logger = logging.getLogger(__name__)

# Claves cuyo valor jamás se guarda en la auditoría.
CLAVES_SENSIBLES = ('password', 'clave', 'secret', 'token', 'authorization', 'refresh', 'access')
MAX_TEXTO = 500
MAX_PROFUNDIDAD = 4


def ip_de(request):
    """IP del cliente. Detrás del proxy de Vite/Nginx llega en X-Forwarded-For."""
    if request is None:
        return None
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = reenviada.split(',')[0].strip() if reenviada else request.META.get('REMOTE_ADDR')
    return (ip or '')[:45] or None


def limpiar(valor, nivel=0):
    """Deja el dato listo para guardar como JSON: sin secretos, con textos cortos y tipos serializables."""
    if nivel > MAX_PROFUNDIDAD:
        return '...'
    if isinstance(valor, dict):
        return {
            str(k): ('***' if any(s in str(k).lower() for s in CLAVES_SENSIBLES) else limpiar(v, nivel + 1))
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [limpiar(v, nivel + 1) for v in list(valor)[:50]]
    if isinstance(valor, str) and len(valor) > MAX_TEXTO:
        return valor[:MAX_TEXTO] + '…'
    # Decimal, fechas, UUID... se convierten a texto de forma estable.
    return json.loads(json.dumps(valor, cls=DjangoJSONEncoder))


def _iguales(a, b):
    # 10 y 10.00 son el mismo importe: los números se comparan por valor, no como texto.
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return str(a) == str(b)


def diferencias(antes, despues):
    """Solo los campos que cambiaron: ({campo: valor_antes}, {campo: valor_despues})."""
    claves = [k for k in despues if not _iguales(antes.get(k), despues.get(k))]
    return {k: antes.get(k) for k in claves}, {k: despues.get(k) for k in claves}


def registrar(request, modulo, accion, tabla, registro_id=None, antes=None, despues=None, usuario=None):
    """
    Guarda un registro de auditoría detallado.
    `request` puede ser None (tareas internas); en ese caso pasar `usuario` si se conoce.
    """
    try:
        autor = usuario or getattr(request, 'user', None)
        if autor is not None and not getattr(autor, 'is_authenticated', False):
            autor = None
        Auditoria.objects.create(
            id_usuario=autor,
            modulo=str(modulo)[:50],
            accion=str(accion)[:50],
            tabla_afectada=str(tabla)[:50],
            registro_id=str(registro_id)[:50] if registro_id is not None else None,
            datos_anteriores=limpiar(antes) if antes is not None else None,
            datos_nuevos=limpiar(despues) if despues is not None else None,
            ip=ip_de(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '')[:300] if request is not None else None),
        )
        if request is not None:
            # Marca la petición para que el registro automático no la duplique.
            django_request = getattr(request, '_request', request)
            django_request._auditoria_registrada = True
    except Exception:  # la auditoría jamás debe interrumpir la operación del usuario
        logger.exception('No se pudo registrar la auditoría (%s %s)', modulo, accion)
