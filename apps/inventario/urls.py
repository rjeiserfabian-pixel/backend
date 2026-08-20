from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoriaViewSet, MarcaRepuestoViewSet, RepuestoViewSet

router = DefaultRouter()
router.register(r'categorias', CategoriaViewSet)
router.register(r'marcas', MarcaRepuestoViewSet)
router.register(r'repuestos', RepuestoViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
