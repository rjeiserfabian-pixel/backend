import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from .models import OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva
from .serializers import (
    OrdenTrabajoListSerializer, OrdenTrabajoDetailSerializer,
    HallazgoSerializer, OrdenServicioSerializer, OrdenRepuestoSerializer,
    PlantillaPreventivaSerializer
)
from apps.inventario.models import MovimientoInventario, InventarioStock

logger = logging.getLogger(__name__)

class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        # Evitar N+1 en las consultas, usando select_related para FK y prefetch para M:N
        queryset = OrdenTrabajo.objects.select_related(
            'vehiculo', 'recepcionista', 'mecanico_asignado'
        ).prefetch_related('vehiculo__clientes')
        
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related(
                'hallazgos', 'servicios', 
                Prefetch('repuestos', queryset=OrdenRepuesto.objects.select_related('repuesto'))
            )
            
        # Filtrado por rol (Mecánico solo ve las suyas)
        user = self.request.user
        if hasattr(user, 'usuario_roles'):
            is_mecanico = user.usuario_roles.filter(id_rol__codigo='MECANICO', estado=True).exists()
            is_admin = user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR', estado=True).exists()
            
            if is_mecanico and not is_admin:
                queryset = queryset.filter(mecanico_asignado=user)
                
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return OrdenTrabajoListSerializer
        return OrdenTrabajoDetailSerializer

    def perform_create(self, serializer):
        # Generar numero de OT unico secuencial
        last_ot = OrdenTrabajo.objects.order_by('-id').first()
        next_num = 1 if not last_ot else last_ot.id + 1
        numero_ot = f"{next_num:06d}"
        
        orden = serializer.save(
            recepcionista=self.request.user,
            numero=numero_ot
        )
        
        if orden.kilometraje_ingreso is not None:
            vehiculo = orden.vehiculo
            vehiculo.kilometraje_actual = orden.kilometraje_ingreso
            vehiculo.save(update_fields=['kilometraje_actual'])

    @action(detail=True, methods=['post'])
    def aprobar_servicios(self, request, pk=None):
        """Endpoint para aprobar masivamente servicios y repuestos luego que el cliente revisa."""
        orden = self.get_object()
        
        # Validar payload
        servicios_ids = request.data.get('servicios_aprobados', [])
        repuestos_ids = request.data.get('repuestos_aprobados', [])
        
        with transaction.atomic():
            # Actualizar servicios
            OrdenServicio.objects.filter(orden=orden, id__in=servicios_ids).update(aprobado_cliente=True)
            OrdenServicio.objects.filter(orden=orden).exclude(id__in=servicios_ids).update(aprobado_cliente=False)
            
            # Actualizar repuestos
            OrdenRepuesto.objects.filter(orden=orden, id__in=repuestos_ids).update(aprobado_cliente=True)
            OrdenRepuesto.objects.filter(orden=orden).exclude(id__in=repuestos_ids).update(aprobado_cliente=False)
            
            orden.estado = OrdenTrabajo.Estado.APROBADO
            orden.save()
            
            logger.info(f"OT-{orden.numero} aprobada por cliente. Servicios: {servicios_ids}, Repuestos: {repuestos_ids}")
            
        return Response({'status': 'ok', 'message': 'Aprobación registrada correctamente.'})

class HallazgoViewSet(viewsets.ModelViewSet):
    queryset = Hallazgo.objects.all()
    serializer_class = HallazgoSerializer
    permission_classes = [IsAuthenticated]
    
    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)

class OrdenServicioViewSet(viewsets.ModelViewSet):
    queryset = OrdenServicio.objects.all()
    serializer_class = OrdenServicioSerializer
    permission_classes = [IsAuthenticated]

class OrdenRepuestoViewSet(viewsets.ModelViewSet):
    queryset = OrdenRepuesto.objects.select_related('repuesto')
    serializer_class = OrdenRepuestoSerializer
    permission_classes = [IsAuthenticated]

class PlantillaPreventivaViewSet(viewsets.ModelViewSet):
    queryset = PlantillaPreventiva.objects.all()
    serializer_class = PlantillaPreventivaSerializer
    permission_classes = [IsAuthenticated]
