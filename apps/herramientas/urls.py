from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views_mantenimientos import (
    AlertasHerramientasView,
    IncidenciaViewSet,
    PlanMantenimientoViewSet,
    RegistroMantenimientoViewSet,
)
from .views_reportes import ReporteHerramientasView, ResumenHerramientasView
from .views import AsignacionViewSet, CategoriaHerramientaViewSet, HerramientaViewSet

router = DefaultRouter()
router.register(r'categorias', CategoriaHerramientaViewSet, basename='herramienta_categoria')
router.register(r'herramientas', HerramientaViewSet, basename='herramienta')
router.register(r'asignaciones', AsignacionViewSet, basename='herramienta_asignacion')
router.register(r'planes-mantenimiento', PlanMantenimientoViewSet, basename='herramienta_plan')
router.register(r'mantenimientos', RegistroMantenimientoViewSet, basename='herramienta_mantenimiento')
router.register(r'incidencias', IncidenciaViewSet, basename='herramienta_incidencia')

urlpatterns = [
    path('alertas/', AlertasHerramientasView.as_view(), name='herramientas_alertas'),
    path('resumen/', ResumenHerramientasView.as_view(), name='herramientas_resumen'),
    path('reportes/<str:tipo>/', ReporteHerramientasView.as_view(), name='herramientas_reporte'),
    path('', include(router.urls)),
]
