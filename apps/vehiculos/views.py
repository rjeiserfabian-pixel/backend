from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import filters
from .models import Vehiculo
from .serializers import VehiculoSerializer
from .services import ConsultaVehicularService
from apps.seguridad.permissions import PermisoPorMetodoMixin
from apps.clientes.models import Cliente
import logging

logger = logging.getLogger(__name__)

class VehiculoViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "VEHICULOS.VER"
    permiso_crear = "VEHICULOS.CREAR"
    permiso_editar = "VEHICULOS.EDITAR"
    permiso_eliminar = "VEHICULOS.ELIMINAR"
    serializer_class = VehiculoSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['placa', 'marca', 'modelo']

    def get_queryset(self):
        # Reglas de Python Seguro: prefetch_related para relación M:N para evitar N+1
        return Vehiculo.objects.filter(estado=True).prefetch_related('clientes').order_by('-id')

    def perform_destroy(self, instance):
        # Soft delete
        instance.estado = False
        instance.save()

    @action(detail=False, methods=['post'], url_path='consulta-placa')
    def consulta_placa(self, request):
        placa = request.data.get('placa')
        if not placa:
            return Response({'error': 'El campo placa es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Eliminar guiones o espacios para normalizar (común en placas)
        placa = placa.replace("-", "").replace(" ", "").upper()
        
        try:
            # Buscar en base local primero para ahorrar cuotas de la API
            vehiculo = Vehiculo.objects.filter(placa=placa).first()
            if vehiculo:
                serializer = self.get_serializer(vehiculo)
                return Response({'origen': 'local', 'data': serializer.data})
            
            # Consultar API Yupay.dev
            servicio = ConsultaVehicularService()
            datos = servicio.consultar_placa(placa)
            return Response({'origen': 'api', 'data': datos})
            
        except ValueError as e:
            logger.warning(f"Error de validación al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno al consultar placa {placa}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='vincular-cliente')
    def vincular_cliente(self, request, pk=None):
        """
        Vincula un cliente a este vehículo de forma aditiva (nunca desvincula
        a otros clientes ya asociados) — un vehículo puede tener varios
        dueños/conductores a lo largo del tiempo (ej. un familiar, alguien
        que lo tomó prestado).
        """
        vehiculo = self.get_object()
        cliente_id = request.data.get('cliente_id')
        if not cliente_id:
            return Response({'error': 'cliente_id es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cliente = Cliente.objects.get(id=cliente_id)
        except Cliente.DoesNotExist:
            return Response({'error': 'Cliente no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        vehiculo.clientes.add(cliente)
        return Response(self.get_serializer(vehiculo).data)

from .models import VehiculoTransporte
from .serializers import VehiculoTransporteSerializer

class VehiculoTransporteViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    # No existe VEHICULOS_TRANSPORTE.ELIMINAR en el catálogo; se reutiliza EDITAR.
    permiso_ver = "VEHICULOS_TRANSPORTE.VER"
    permiso_crear = "VEHICULOS_TRANSPORTE.CREAR"
    permiso_editar = "VEHICULOS_TRANSPORTE.EDITAR"
    serializer_class = VehiculoTransporteSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['placa', 'marca', 'modelo']

    def get_queryset(self):
        return VehiculoTransporte.objects.filter(estado=True).order_by('-id')

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()

    @action(detail=False, methods=['post'], url_path='consulta-placa')
    def consulta_placa(self, request):
        placa = request.data.get('placa')
        if not placa:
            return Response({'error': 'El campo placa es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)
        
        placa = placa.replace("-", "").replace(" ", "").upper()
        
        try:
            # Buscar en base local primero
            vehiculo = VehiculoTransporte.objects.filter(placa=placa).first()
            if vehiculo:
                serializer = self.get_serializer(vehiculo)
                return Response({'origen': 'local', 'data': serializer.data})
            
            # Consultar API Yupay.dev
            servicio = ConsultaVehicularService()
            datos = servicio.consultar_placa(placa)
            return Response({'origen': 'api', 'data': datos})
            
        except ValueError as e:
            logger.warning(f"Error de validación al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno al consultar placa {placa}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

