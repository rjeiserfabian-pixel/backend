from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    CajaViewSet, SesionCajaViewSet, MetodoPagoViewSet, 
    ImpuestoViewSet, TipoComprobanteViewSet, SerieComprobanteViewSet, VentaViewSet,
    CuentaPorCobrarViewSet, TipoCambioView
)

router = DefaultRouter()
router.register(r'cajas', CajaViewSet, basename='caja')
router.register(r'sesiones', SesionCajaViewSet, basename='sesioncaja')
router.register(r'metodos-pago', MetodoPagoViewSet, basename='metodopago')
router.register(r'impuestos', ImpuestoViewSet, basename='impuesto')
router.register(r'tipos-comprobante', TipoComprobanteViewSet, basename='tipocomprobante')
router.register(r'series-comprobante', SerieComprobanteViewSet, basename='seriecomprobante')
router.register(r'transacciones', VentaViewSet, basename='venta')
router.register(r'cuentas-por-cobrar', CuentaPorCobrarViewSet, basename='cuentaporcobrar')

urlpatterns = [
    path('tipo-cambio/', TipoCambioView.as_view(), name='tipo-cambio'),
    path('', include(router.urls)),
]
