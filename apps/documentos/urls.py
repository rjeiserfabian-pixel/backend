from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DocumentoViewSet

router = DefaultRouter()
router.register(r'', DocumentoViewSet, basename='documento')

urlpatterns = [
    path('', include(router.urls)),
]
