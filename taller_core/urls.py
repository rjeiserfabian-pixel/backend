"""
urls.py — Enrutador principal del proyecto.
Solo incluye los urls.py de cada app (módulo).
Nunca define endpoints directamente aquí.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # Módulo de Seguridad — /api/seguridad/...
    path("api/seguridad/", include("apps.seguridad.urls")),
    # Nuevos Módulos
    path("api/inventario/", include("apps.inventario.urls")),
    path("api/vehiculos/", include("apps.vehiculos.urls")),
    path("api/clientes/", include("apps.clientes.urls")),
    path("api/ventas/", include("apps.ventas.urls")),
    path("api/taller/", include("apps.taller.urls")),
    path("api/compras/", include("apps.compras.urls")),
    path("api/reportes/", include("apps.reportes.urls")),
    path("api/cajas/", include("apps.cajas.urls")),
    path("api/facturacion/", include("apps.facturacion.urls")),
    path("api/herramientas/", include("apps.herramientas.urls")),
    path("api/documentos/", include("apps.documentos.urls")),
    path("api/avisos/", include("apps.avisos.urls")),
]

import posixpath

from django.conf import settings
from django.http import Http404
from django.urls import re_path
from django.views.static import serve

# MEDIA_ROOT es la raíz del backend (ahí viven .env, settings.py, etc.), así que NUNCA se
# sirve completa: solo las carpetas de imágenes públicas (logo de la empresa, avatares y
# QR de pago). Los documentos privados (SOAT, licencias) van por /api/documentos/.
CARPETAS_MEDIA_PUBLICAS = ('empresa', 'avatars', 'pagos_qr')


def media_publica(request, path):
    limpio = posixpath.normpath(path).lstrip('/')  # resuelve "empresa/../.env" antes de comprobar
    partes = limpio.split('/')
    if '..' in partes or len(partes) < 2 or partes[0] not in CARPETAS_MEDIA_PUBLICAS:
        raise Http404('Archivo no disponible.')
    return serve(request, limpio, document_root=settings.MEDIA_ROOT)


# Se sirve también con DEBUG=False: el sistema se ejecuta con Waitress, sin otro servidor web.
urlpatterns += [re_path(r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'), media_publica)]

