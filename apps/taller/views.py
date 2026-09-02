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
from apps.ventas.models import Venta, DetalleVenta
import uuid

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
        
        if orden.cliente:
            orden.vehiculo.clientes.add(orden.cliente)
        
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
            
            # Actualizar repuestos y reservar stock
            repuestos_a_aprobar = OrdenRepuesto.objects.filter(orden=orden, id__in=repuestos_ids)
            for orp in repuestos_a_aprobar:
                if not orp.aprobado_cliente:  # Solo si no estaba aprobado antes
                    orp.aprobado_cliente = True
                    orp.save(update_fields=['aprobado_cliente'])
                    
                    # Reservar stock
                    stock_record = InventarioStock.objects.filter(repuesto=orp.repuesto, stock_disponible__gte=orp.cantidad).first()
                    if not stock_record:
                        stock_record = InventarioStock.objects.filter(repuesto=orp.repuesto).first()
                    
                    if stock_record:
                        stock_record.stock_disponible -= orp.cantidad
                        stock_record.stock_reservado += orp.cantidad
                        stock_record.save()
                        
                        MovimientoInventario.objects.create(
                            repuesto=orp.repuesto,
                            ubicacion=stock_record.ubicacion,
                            tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                            cantidad=-orp.cantidad,
                            stock_resultante=stock_record.stock_disponible,
                            motivo=f"Reserva para OT-{orden.numero}",
                            usuario=request.user,
                            referencia_id=orden.id,
                            referencia_tipo='OT'
                        )
            
            # Desaprobar los no seleccionados
            OrdenRepuesto.objects.filter(orden=orden).exclude(id__in=repuestos_ids).update(aprobado_cliente=False)
            
            orden.estado = OrdenTrabajo.Estado.APROBADO
            orden.save()
            
            logger.info(f"OT-{orden.numero} aprobada por cliente. Servicios: {servicios_ids}, Repuestos: {repuestos_ids}")
            
        return Response({'status': 'ok', 'message': 'Aprobación registrada correctamente.'})

    @action(detail=True, methods=['post'])
    def finalizar_orden(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.APROBADO:
            return Response({'error': 'La orden debe estar en estado APROBADO para finalizarse.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar servicios aprobados completados
        servicios_aprobados = orden.servicios.filter(aprobado_cliente=True)
        if servicios_aprobados.filter(completado=False).exists():
            return Response({'error': 'Todos los servicios aprobados deben estar marcados como Terminados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar repuestos aprobados instalados
        repuestos_aprobados = orden.repuestos.filter(aprobado_cliente=True)
        if repuestos_aprobados.filter(instalado=False).exists():
            return Response({'error': 'Todos los repuestos aprobados deben estar marcados como Instalados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Cambiar estado
        orden.estado = OrdenTrabajo.Estado.FINALIZADO
        orden.fecha_finalizacion = timezone.now()
        orden.save(update_fields=['estado', 'fecha_finalizacion'])
        
        return Response({'status': 'ok', 'message': 'Orden finalizada correctamente.', 'estado': orden.estado})

    @action(detail=True, methods=['post'])
    def enviar_a_pos(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.FINALIZADO:
            return Response({'error': 'La orden debe estar en estado FINALIZADO para enviarse a POS.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if not orden.cliente:
            return Response({'error': 'La orden no tiene un cliente asignado. Asigne un cliente en los detalles de la orden antes de cobrar.'}, status=status.HTTP_400_BAD_REQUEST)
            
        sucursal_id = request.data.get('sucursal_id')
        if not sucursal_id:
            # Intentar obtener de request.user si es necesario o por defecto 1
            sucursal_id = 1
            
        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).first()
        
        if venta_existente:
            return Response({
                'status': 'ok', 
                'message': 'Ya existe un ticket en POS.', 
                'venta_id': venta_existente.id,
                'ticket': venta_existente.ticket_kiosko,
                'estado_orden': orden.estado
            })
            
        with transaction.atomic():
            ticket_code = f"OT-{orden.id}-{str(uuid.uuid4())[:4].upper()}"
            venta = Venta.objects.create(
                cliente=orden.cliente,
                vehiculo=orden.vehiculo,
                sucursal_id=sucursal_id,
                estado=Venta.Estado.PRE_VENTA,
                ticket_kiosko=ticket_code,
                kilometraje=orden.kilometraje_ingreso
            )
            
            subtotal_acumulado = 0
            
            # Repuestos
            for rep in orden.repuestos.filter(aprobado_cliente=True, instalado=True):
                subtotal_linea = rep.cantidad * rep.precio_unitario
                DetalleVenta.objects.create(
                    venta=venta,
                    repuesto=rep.repuesto,
                    cantidad=rep.cantidad,
                    precio_unitario=rep.precio_unitario,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            # Servicios
            for serv in orden.servicios.filter(aprobado_cliente=True, completado=True):
                subtotal_linea = serv.precio_estimado
                # Cantidad = 1, usando descripcion_servicio
                DetalleVenta.objects.create(
                    venta=venta,
                    descripcion_servicio=serv.descripcion,
                    cantidad=1,
                    precio_unitario=serv.precio_estimado,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            from decimal import Decimal
            venta.total = subtotal_acumulado
            venta.subtotal = venta.total / Decimal('1.18')  # TODO: usar tipo impuesto
            venta.igv = venta.total - venta.subtotal
            venta.save()
            
            # NOTA: Ya no cambiamos a FACTURADO aquí, se hará cuando se pague en POS.
            
        return Response({
            'status': 'ok', 
            'message': 'Enviado a POS correctamente.', 
            'venta_id': venta.id,
            'ticket': venta.ticket_kiosko,
            'estado_orden': orden.estado
        })

    @action(detail=True, methods=['get'])
    def generar_pdf(self, request, pk=None):
        orden = self.get_object()
        
        # Calcular totales
        total_servicios = sum(s.precio_estimado for s in orden.servicios.all())
        total_repuestos = sum(r.cantidad * r.precio_unitario for r in orden.repuestos.all())
        total_general = total_servicios + total_repuestos
        
        # Configurar contexto
        context = {
            'orden': orden,
            'total_servicios': total_servicios,
            'total_repuestos': total_repuestos,
            'total_general': total_general,
            'empresa': {
                'nombre': 'OMEGA AUTOMOTRIZ',
                'direccion': 'Av. Principal 123',
                'ruc': '20123456789',
                'telefono': '987-654-321'
            }
        }
        
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from xhtml2pdf import pisa
        import io
        
        html_string = render_to_string('taller/proforma_pdf.html', context)
        
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cotizacion_OT_{orden.numero}.pdf"'
        
        pisa_status = pisa.CreatePDF(
            html_string, dest=response
        )
        
        if pisa_status.err:
            return HttpResponse('Error generando PDF', status=500)
            
        return response

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

    @action(detail=True, methods=['patch'])
    def marcar_completado(self, request, pk=None):
        servicio = self.get_object()
        servicio.completado = not servicio.completado
        servicio.save(update_fields=['completado'])
        return Response({'status': 'ok', 'completado': servicio.completado})

class OrdenRepuestoViewSet(viewsets.ModelViewSet):
    queryset = OrdenRepuesto.objects.select_related('repuesto')
    serializer_class = OrdenRepuestoSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['patch'])
    def marcar_instalado(self, request, pk=None):
        repuesto_orden = self.get_object()
        repuesto_orden.instalado = not repuesto_orden.instalado
        repuesto_orden.save(update_fields=['instalado'])
        
        # Convertir reserva en salida definitiva al instalar
        if repuesto_orden.instalado:
            stock_record = InventarioStock.objects.filter(repuesto=repuesto_orden.repuesto).first()
            if stock_record:
                stock_record.stock_reservado -= repuesto_orden.cantidad
                stock_record.save()
                MovimientoInventario.objects.create(
                    repuesto=repuesto_orden.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                    cantidad=-repuesto_orden.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Instalación en OT-{repuesto_orden.orden.numero}",
                    usuario=request.user,
                    referencia_id=repuesto_orden.orden.id,
                    referencia_tipo='OT'
                )
                
        return Response({'status': 'ok', 'instalado': repuesto_orden.instalado})

class PlantillaPreventivaViewSet(viewsets.ModelViewSet):
    queryset = PlantillaPreventiva.objects.all()
    serializer_class = PlantillaPreventivaSerializer
    permission_classes = [IsAuthenticated]
