from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrdenTrabajoViewSet, HallazgoViewSet, 
    OrdenServicioViewSet, OrdenRepuestoViewSet,
    PlantillaPreventivaViewSet, ConsultaVehiculoPublicaView
)

router = DefaultRouter()
router.register(r'ordenes', OrdenTrabajoViewSet, basename='orden_trabajo')
router.register(r'hallazgos', HallazgoViewSet, basename='hallazgo')
router.register(r'servicios', OrdenServicioViewSet, basename='servicio')
router.register(r'repuestos', OrdenRepuestoViewSet, basename='repuesto')
router.register(r'plantillas', PlantillaPreventivaViewSet, basename='plantilla')

urlpatterns = [
    path('public/consulta-vehiculo/', ConsultaVehiculoPublicaView.as_view(), name='consulta_vehiculo_publica'),
    path('', include(router.urls)),
]
