from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ConsultaVehiculoQRPublicaView, VehiculoViewSet, VehiculoTransporteViewSet
from .public_views import ConsultaPlacaPublicaView

router = DefaultRouter()
router.register(r'transporte', VehiculoTransporteViewSet, basename='vehiculo_transporte')
router.register(r'', VehiculoViewSet, basename='vehiculo')

urlpatterns = [
    path('public/consultar-placa/', ConsultaPlacaPublicaView.as_view(), name='consulta_placa_publica'),
    path('public/qr/<str:token>/', ConsultaVehiculoQRPublicaView.as_view(), name='vehiculo_qr_publico'),
    path('', include(router.urls)),
]
