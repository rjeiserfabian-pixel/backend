from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import filters
from .models import Vehiculo
from .serializers import VehiculoSerializer
from .services import ConsultaVehicularService
import logging

logger = logging.getLogger(__name__)

class VehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = VehiculoSerializer
    permission_classes = [IsAuthenticated]
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

from .models import VehiculoTransporte
from .serializers import VehiculoTransporteSerializer

class VehiculoTransporteViewSet(viewsets.ModelViewSet):
    serializer_class = VehiculoTransporteSerializer
    permission_classes = [IsAuthenticated]
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

