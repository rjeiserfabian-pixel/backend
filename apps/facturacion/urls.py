from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ComprobanteElectronicoViewSet

router = DefaultRouter()
router.register(r'comprobantes', ComprobanteElectronicoViewSet, basename='comprobanteelectronico')

urlpatterns = [
    path('', include(router.urls)),
]
