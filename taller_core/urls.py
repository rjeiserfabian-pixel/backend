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
]
