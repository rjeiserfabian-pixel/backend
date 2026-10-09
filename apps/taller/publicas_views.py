import logging
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from .models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from .services import aprobar_cotizacion_orden

logger = logging.getLogger(__name__)


class ConsultaVehiculoPublicaView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        placa = request.data.get('placa')
        dni = request.data.get('dni')
        sucursal_id = request.data.get('sucursal_id')
        
        if not placa or not dni:
            return Response({'error': 'Placa y DNI son requeridos'}, status=status.HTTP_400_BAD_REQUEST)
            
        vehiculo = Vehiculo.objects.filter(placa__iexact=placa).first()
        if not vehiculo:
            return Response({'error': 'Vehículo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
            
        # Buscar la última orden activa (que no esté FACTURADA ni CANCELADA)
        ordenes_qs = (
            OrdenTrabajo.objects
            .filter(vehiculo=vehiculo)
            .exclude(estado__in=['FACTURADO', 'CANCELADO'])
            .prefetch_related('hallazgos')
        )
        if sucursal_id:
            ordenes_qs = ordenes_qs.filter(
                recepcionista__sucursales_asignadas__sucursal_id=sucursal_id,
                recepcionista__sucursales_asignadas__estado=True,
            ).distinct()
        orden = ordenes_qs.order_by('-fecha_ingreso').first()
        
        # Validar credenciales: El DNI debe ser del dueño (vehiculo.clientes) o del cliente que dejó la orden activa (orden.cliente.dni)
        es_propietario = vehiculo.clientes.filter(dni=dni).exists()
        es_cliente_orden = orden and orden.cliente and orden.cliente.dni == dni
        
        if not es_propietario and not es_cliente_orden:
            return Response({'error': 'Vehículo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
            
        
        # Helper para obtener el nombre del cliente
        def get_nombre_cliente(c):
            if c:
                return f"{c.nombres} {c.apellidos}".strip()
            return 'Cliente'

        vehiculo_data = {
            'placa': vehiculo.placa,
            'marca': vehiculo.marca,
            'modelo': vehiculo.modelo,
            'cliente': get_nombre_cliente(orden.cliente) if orden and orden.cliente else get_nombre_cliente(vehiculo.clientes.first()) if vehiculo.clientes.exists() else 'Cliente',
        }
        
        if not orden:
            return Response({
                'vehiculo': vehiculo_data,
                'has_active_order': False,
                'message': 'Su vehículo no tiene reparaciones activas'
            })
            
        cotizacion_pendiente = orden.estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION

        # Preparar resumen de la orden
        servicios_qs = orden.servicios.all() if cotizacion_pendiente else orden.servicios.filter(aprobado_cliente=True)
        servicios = [
            {
                'id': s.id,
                'descripcion': s.descripcion,
                'completado': s.completado,
                'precio': s.precio_estimado,
                'aprobado_cliente': s.aprobado_cliente
            }
            for s in servicios_qs
        ]

        repuestos_qs = orden.repuestos.select_related('repuesto')
        if not cotizacion_pendiente:
            repuestos_qs = repuestos_qs.filter(aprobado_cliente=True)
        repuestos = [
            {
                'id': r.id,
                'descripcion': r.repuesto.nombre if r.repuesto else 'Repuesto',
                'instalado': r.instalado,
                'precio': r.precio_unitario,
                'cantidad': r.cantidad,
                'aprobado_cliente': r.aprobado_cliente
            }
            for r in repuestos_qs
        ]

        hallazgos = [
            {
                'id': h.id,
                'descripcion': h.descripcion,
                'severidad': h.severidad,
                'fecha_registro': h.fecha_registro,
            }
            for h in orden.hallazgos.all().order_by('-fecha_registro')
        ]
        
        total_estimado = sum([float(s['precio']) for s in servicios]) + sum([float(r['precio']) * float(r['cantidad']) for r in repuestos])
        cotizacion_vencida = bool(orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion)
        
        return Response({
            'vehiculo': vehiculo_data,
            'has_active_order': True,
            'orden': {
                'id': orden.id,
                'numero': orden.numero,
                'estado': orden.estado,
                'fecha_ingreso': orden.fecha_ingreso,
                'fecha_vencimiento_cotizacion': orden.fecha_vencimiento_cotizacion,
                'cotizacion_pendiente': cotizacion_pendiente,
                'cotizacion_vencida': cotizacion_vencida,
                'hallazgos': hallazgos,
                'servicios': servicios,
                'repuestos': repuestos,
                'total_estimado': total_estimado
            }
        })


class AprobarCotizacionPublicaView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        placa = request.data.get('placa')
        dni = request.data.get('dni')
        sucursal_id = request.data.get('sucursal_id')

        if not placa or not dni:
            return Response({'error': 'Placa y DNI son requeridos'}, status=status.HTTP_400_BAD_REQUEST)

        vehiculo = Vehiculo.objects.filter(placa__iexact=placa).first()
        if not vehiculo:
            return Response({'error': 'Vehiculo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)

        ordenes_qs = (
            OrdenTrabajo.objects
            .filter(vehiculo=vehiculo)
            .exclude(estado__in=['FACTURADO', 'CANCELADO'])
        )
        if sucursal_id:
            ordenes_qs = ordenes_qs.filter(
                recepcionista__sucursales_asignadas__sucursal_id=sucursal_id,
                recepcionista__sucursales_asignadas__estado=True,
            ).distinct()
        orden = ordenes_qs.order_by('-fecha_ingreso').first()

        es_propietario = vehiculo.clientes.filter(dni=dni).exists()
        es_cliente_orden = orden and orden.cliente and orden.cliente.dni == dni

        if not es_propietario and not es_cliente_orden:
            return Response({'error': 'Vehiculo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
        if not orden:
            return Response({'error': 'No hay una orden activa para aprobar.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            aprobar_cotizacion_orden(
                orden,
                request.data.get('servicios_aprobados', []),
                request.data.get('repuestos_aprobados', []),
                usuario=None,
                observaciones='Aprobado por el cliente desde Estado de Vehiculo.',
                exigir_esperando_aprobacion=True,
                exigir_seleccion=True,
            )
        except ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            return Response({'error': detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'ok',
            'message': 'Cotizacion aprobada correctamente.',
            'estado': OrdenTrabajo.Estado.APROBADO,
        })
