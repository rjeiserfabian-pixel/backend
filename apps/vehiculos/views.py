from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import filters
from rest_framework.permissions import AllowAny
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

    @action(
        detail=False, methods=['get'],
        url_path='kiosko/buscar-vehiculo',
        permission_classes=[AllowAny]  # Público: el kiosko opera sin sesión
    )
    def kiosko_buscar_vehiculo(self, request):
        """
        Endpoint público para el kiosko. Busca un vehículo por placa:
        primero en la BD local, luego en la API externa (SunRP/Yupay).
        """
        placa = request.query_params.get('placa', '').strip().replace('-', '').replace(' ', '').upper()
        if not placa or len(placa) < 6:
            return Response(
                {'error': 'La placa debe tener al menos 6 caracteres.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            vehiculo = Vehiculo.objects.filter(placa=placa, estado=True).prefetch_related('clientes').first()
            if vehiculo:
                serializer = self.get_serializer(vehiculo)
                return Response({'origen': 'local', 'data': serializer.data})

            servicio = ConsultaVehicularService()
            datos = servicio.consultar_placa(placa)
            return Response({'origen': 'api', 'data': datos})

        except ValueError as e:
            logger.warning(f"[Kiosko] Error al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"[Kiosko] Error de conexión al consultar placa {placa}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"[Kiosko] Error inesperado al consultar placa {placa}: {e}", exc_info=True)
            return Response(
                {'error': 'Error interno al consultar la placa.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(
        detail=False, methods=['post'],
        url_path='kiosko/crear-vehiculo',
        permission_classes=[AllowAny]  # Público: el kiosko opera sin sesión
    )
    def kiosko_crear_vehiculo(self, request):
        """
        Endpoint público para que el kiosko registre un vehículo nuevo
        cuando la placa no existe en la BD.
        """
        serializer = VehiculoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vehiculo = serializer.save()
        logger.info(f"[Kiosko] Vehículo creado desde terminal pública: {vehiculo.id} - Placa: {vehiculo.placa}")
        return Response(VehiculoSerializer(vehiculo).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=['post'],
        url_path='kiosko/vincular-cliente',
        permission_classes=[AllowAny]  # Público: el kiosko opera sin sesión
    )
    def kiosko_vincular_cliente(self, request, pk=None):
        """
        Endpoint público para vincular un cliente a un vehículo existente
        desde el kiosko.
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

    @action(detail=False, methods=['post'], url_path='consulta-placa')
    def consulta_placa(self, request):
        placa = request.data.get('placa')
        if not placa:
            return Response({'error': 'El campo placa es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Eliminar guiones o espacios para normalizar (común en placas)
        placa = placa.replace("-", "").replace(" ", "").upper()
        
        try:
            # Buscar en base local primero para ahorrar cuotas de la API.
            # estado=True: un vehículo eliminado (soft-delete) no debe
            # "resucitar" con datos viejos/manuales en vez de re-consultar
            # las APIs externas.
            vehiculo = Vehiculo.objects.filter(placa=placa, estado=True).first()
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
    permiso_ver = "VEHICULOS_TRANSPORTE.VER"
    permiso_crear = "VEHICULOS_TRANSPORTE.CREAR"
    permiso_editar = "VEHICULOS_TRANSPORTE.EDITAR"
    permiso_eliminar = "VEHICULOS_TRANSPORTE.ELIMINAR"
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
            # Buscar en base local primero. estado=True: un vehículo eliminado
            # (soft-delete) no debe "resucitar" con datos viejos en vez de
            # re-consultar las APIs externas.
            vehiculo = VehiculoTransporte.objects.filter(placa=placa, estado=True).first()
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

