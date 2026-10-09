from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AvisoViewSet

router = DefaultRouter()
router.register(r'', AvisoViewSet, basename='aviso')

urlpatterns = [
    path('', include(router.urls)),
]
