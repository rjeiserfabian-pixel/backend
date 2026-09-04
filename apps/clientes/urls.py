from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, ProveedorViewSet, TransportistaViewSet

router = DefaultRouter()
router.register(r'proveedores', ProveedorViewSet, basename='proveedor')
router.register(r'transportistas', TransportistaViewSet, basename='transportista')
router.register(r'', ClienteViewSet, basename='cliente')

urlpatterns = [
    path('', include(router.urls)),
]
