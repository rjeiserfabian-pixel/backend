from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VehiculoViewSet, VehiculoTransporteViewSet

router = DefaultRouter()
router.register(r'transporte', VehiculoTransporteViewSet, basename='vehiculo_transporte')
router.register(r'', VehiculoViewSet, basename='vehiculo')

urlpatterns = [
    path('', include(router.urls)),
]
