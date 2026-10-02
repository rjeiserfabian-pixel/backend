from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    BloqueoAgendaSucursalViewSet, CitaViewSet, ConfiguracionAgendaSucursalViewSet,
    CitasCatalogoPublicoView, DisponibilidadCitasPublicaView, ListaEsperaCitaViewSet,
    OrdenTrabajoViewSet, HallazgoViewSet, 
    OrdenServicioViewSet, OrdenRepuestoViewSet,
    PlantillaPreventivaViewSet, PlantillaCorrectivaViewSet, ReservaCitaPublicaView,
    ConsultaVehiculoPublicaView, AprobarCotizacionPublicaView, TipoServicioViewSet
)

router = DefaultRouter()
router.register(r'citas', CitaViewSet, basename='cita')
router.register(r'lista-espera-citas', ListaEsperaCitaViewSet, basename='lista_espera_cita')
router.register(r'configuracion-agenda', ConfiguracionAgendaSucursalViewSet, basename='configuracion_agenda')
router.register(r'bloqueos-agenda', BloqueoAgendaSucursalViewSet, basename='bloqueo_agenda')
router.register(r'ordenes', OrdenTrabajoViewSet, basename='orden_trabajo')
router.register(r'hallazgos', HallazgoViewSet, basename='hallazgo')
router.register(r'servicios', OrdenServicioViewSet, basename='servicio')
router.register(r'repuestos', OrdenRepuestoViewSet, basename='repuesto')
router.register(r'plantillas', PlantillaPreventivaViewSet, basename='plantilla')
router.register(r'plantillas-correctivas', PlantillaCorrectivaViewSet, basename='plantilla_correctiva')
router.register(r'tipos-servicio', TipoServicioViewSet, basename='tipo_servicio')

urlpatterns = [
    path('public/citas/catalogo/', CitasCatalogoPublicoView.as_view(), name='citas_catalogo_publico'),
    path('public/citas/disponibilidad/', DisponibilidadCitasPublicaView.as_view(), name='citas_disponibilidad_publica'),
    path('public/citas/reservar/', ReservaCitaPublicaView.as_view(), name='citas_reservar_publica'),
    path('public/consulta-vehiculo/', ConsultaVehiculoPublicaView.as_view(), name='consulta_vehiculo_publica'),
    path('public/aprobar-cotizacion/', AprobarCotizacionPublicaView.as_view(), name='aprobar_cotizacion_publica'),
    path('', include(router.urls)),
]
