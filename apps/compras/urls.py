from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CompraViewSet, CuentaPorPagarViewSet, PagoCuentaViewSet, TipoComprobanteCompraViewSet

router = DefaultRouter()
router.register(r'compras', CompraViewSet, basename='compras')
router.register(r'cuentas-por-pagar', CuentaPorPagarViewSet, basename='cuentas-por-pagar')
router.register(r'pagos-cuenta', PagoCuentaViewSet, basename='pagos-cuenta')
router.register(r'tipos-comprobante', TipoComprobanteCompraViewSet, basename='tipos-comprobante')

urlpatterns = [
    path('', include(router.urls)),
]
