"""
exceptions.py — Manejador global de excepciones para DRF.

Regla: Las excepciones nunca exponen detalles internos al cliente
(stack traces, nombres de tablas, rutas del servidor).
El detalle se loguea internamente; el cliente recibe un mensaje genérico.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Manejador personalizado de excepciones para DRF.
    - Loguea el error completo internamente con contexto.
    - Devuelve al cliente solo un mensaje controlado.
    """
    response = exception_handler(exc, context)

    if response is not None:
        # El error ya fue manejado por DRF (ej. 400, 401, 403, 404)
        # Enriquecemos el formato de la respuesta
        view = context.get("view")
        logger.warning(
            "Error controlado: %s | Vista: %s | Status: %s",
            str(exc),
            view.__class__.__name__ if view else "desconocida",
            response.status_code,
        )
        response.data = {
            "success": False,
            "status_code": response.status_code,
            "mensaje": _get_mensaje_amigable(response.status_code, response.data),
            "errores": response.data,
        }
    else:
        # Error no manejado (500) — loguear con detalle, responder genéricamente
        logger.error(
            "Error no controlado: %s | Vista: %s",
            str(exc),
            context.get("view", "desconocida"),
            exc_info=True,
        )
        response = Response(
            {
                "success": False,
                "status_code": 500,
                "mensaje": "Ocurrió un error interno. Por favor, contacte al administrador.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response


def _get_mensaje_amigable(status_code, data):
    mensajes = {
        400: "Los datos enviados no son válidos.",
        401: "Debe iniciar sesión para acceder a este recurso.",
        403: "No tiene permisos para realizar esta acción.",
        404: "El recurso solicitado no fue encontrado.",
        405: "Método no permitido.",
        429: "Demasiadas solicitudes. Por favor, espere un momento.",
    }
    return mensajes.get(status_code, "Ha ocurrido un error.")
