"""
urls.py — Módulo de Reportes
Registra todas las rutas de reportes bajo el prefijo /api/reportes/
"""
from django.urls import path
from .views import (
    ReporteCajaView,
    ReporteVentasView,
    ReporteProductosView,
    ReporteClientesView,
    ReporteComprasView,
    ReporteAvanzadoView,
    ReporteVehiculosView,
    FiltrosAuxiliaresView,
)

urlpatterns = [
    path("caja/",       ReporteCajaView.as_view(),       name="reporte-caja"),
    path("ventas/",     ReporteVentasView.as_view(),     name="reporte-ventas"),
    path("productos/",  ReporteProductosView.as_view(),  name="reporte-productos"),
    path("clientes/",   ReporteClientesView.as_view(),   name="reporte-clientes"),
    path("compras/",    ReporteComprasView.as_view(),     name="reporte-compras"),
    path("avanzado/",   ReporteAvanzadoView.as_view(),   name="reporte-avanzado"),
    path("vehiculos/",  ReporteVehiculosView.as_view(),  name="reporte-vehiculos"),
    path("filtros/",    FiltrosAuxiliaresView.as_view(), name="reporte-filtros"),
]
