from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UnidadMedidaViewSet, CategoriaViewSet, MarcaRepuestoViewSet, RepuestoViewSet,
    SucursalViewSet, AlmacenViewSet, UbicacionFisicaViewSet,
    InventarioStockViewSet, MovimientoInventarioViewSet, TrasladoInventarioViewSet,
    GuiaRemisionViewSet
)

router = DefaultRouter()

# Rutas existentes (sin cambios)
router.register(r'unidades-medida', UnidadMedidaViewSet, basename='unidadmedida')
router.register(r'categorias', CategoriaViewSet)
router.register(r'marcas', MarcaRepuestoViewSet)
router.register(r'repuestos', RepuestoViewSet)

# Nuevas rutas: Estructura física multi-almacén
router.register(r'sucursales', SucursalViewSet)
router.register(r'almacenes', AlmacenViewSet)
router.register(r'ubicaciones', UbicacionFisicaViewSet)

# Nuevas rutas: Stock y Kardex
router.register(r'stock', InventarioStockViewSet)
router.register(r'kardex', MovimientoInventarioViewSet)
router.register(r'traslados', TrasladoInventarioViewSet, basename='traslado')
router.register(r'guias-remision', GuiaRemisionViewSet, basename='guia-remision')

urlpatterns = [
    path('', include(router.urls)),
]
