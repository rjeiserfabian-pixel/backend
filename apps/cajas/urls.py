"""
urls.py — Módulo de Cajas
Prefijo: /api/cajas/
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DashboardCajasView,
    CajaViewSet,
    SesionCajaViewSet,
    MovimientoManualViewSet,
    TransferenciaViewSet,
    ArqueoView,
)

router = DefaultRouter()
router.register(r'lista',        CajaViewSet,            basename='caja-modulo')
router.register(r'sesiones',     SesionCajaViewSet,       basename='sesion-cajas')
router.register(r'movimientos',  MovimientoManualViewSet,  basename='movimiento-cajas')
router.register(r'transferencias', TransferenciaViewSet,  basename='transferencia-cajas')

urlpatterns = [
    path('dashboard/',                     DashboardCajasView.as_view(),         name='cajas-dashboard'),
    path('arqueo/<int:sesion_id>/calcular/', ArqueoView.as_view(),                name='arqueo-calcular'),
    path('arqueo/<int:sesion_id>/registrar/', ArqueoView.as_view(),               name='arqueo-registrar'),
    path('', include(router.urls)),
]
