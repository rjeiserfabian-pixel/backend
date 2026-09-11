import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from .models import OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva, TipoServicio, OrdenHistorialEstado
from apps.vehiculos.models import Vehiculo
from .serializers import (
    OrdenTrabajoListSerializer, OrdenTrabajoDetailSerializer,
    HallazgoSerializer, OrdenServicioSerializer, OrdenRepuestoSerializer,
    PlantillaPreventivaSerializer, TipoServicioSerializer
)
from apps.inventario.models import MovimientoInventario, InventarioStock
from apps.ventas.models import Venta, DetalleVenta
from apps.ventas.services import VentasService
from apps.seguridad.permissions import TienePermiso
import uuid

logger = logging.getLogger(__name__)


class TipoServicioViewSet(viewsets.ModelViewSet):
    queryset = TipoServicio.objects.all()
    serializer_class = TipoServicioSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("TIPOS_SERVICIO.VER")]
        # No existe un código ELIMINAR dedicado para Tipos de Servicio; se reutiliza EDITAR.
        return [TienePermiso("TIPOS_SERVICIO.CREAR" if self.request.method == 'POST' else "TIPOS_SERVICIO.EDITAR")]

class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action in ('aprobar_servicios', 'finalizar_orden', 'enviar_a_pos'):
            return [TienePermiso("ORDENES_TRABAJO.APROBAR")]
        if self.action == 'anular':
            return [TienePermiso("ORDENES_TRABAJO.CAMBIAR_ESTADO")]
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("ORDENES_TRABAJO.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

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

        params = self.request.query_params
        estado = params.get('estado')
        mecanico_asignado = params.get('mecanico_asignado')
        cliente = params.get('cliente')
        placa = params.get('placa')
        fecha_desde = params.get('fecha_desde')
        fecha_hasta = params.get('fecha_hasta')

        if estado:
            queryset = queryset.filter(estado=estado)
        if mecanico_asignado:
            queryset = queryset.filter(mecanico_asignado_id=mecanico_asignado)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        if placa:
            queryset = queryset.filter(vehiculo__placa__icontains=placa)
        if fecha_desde:
            queryset = queryset.filter(fecha_ingreso__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_ingreso__date__lte=fecha_hasta)

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
            
        # Generar primer historial de estado
        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=self.request.user
        )

    # Transiciones de estado permitidas fuera de las acciones dedicadas (aprobar_servicios,
    # finalizar_orden, enviar_a_pos, anular). Cualquier otro cambio de estado vía PATCH/PUT
    # directo se rechaza para evitar saltarse las validaciones de negocio de esas acciones.
    TRANSICIONES_MANUALES_PERMITIDAS = {
        (OrdenTrabajo.Estado.RECEPCIONADO, OrdenTrabajo.Estado.INSPECCION),
        (OrdenTrabajo.Estado.INSPECCION, OrdenTrabajo.Estado.ESPERANDO_APROBACION),
    }

    def perform_update(self, serializer):
        orden_anterior = self.get_object()
        estado_anterior = orden_anterior.estado
        nuevo_estado = serializer.validated_data.get('estado', estado_anterior)

        if nuevo_estado != estado_anterior and (estado_anterior, nuevo_estado) not in self.TRANSICIONES_MANUALES_PERMITIDAS:
            raise ValidationError(
                f"No se puede cambiar el estado de '{estado_anterior}' a '{nuevo_estado}' directamente. "
                "Usa la acción correspondiente (aprobar, finalizar, enviar a POS o anular)."
            )

        orden = serializer.save()
        
        # Guardar en el historial si el estado cambió
        if estado_anterior != orden.estado:
            OrdenHistorialEstado.objects.create(
                orden=orden,
                estado=orden.estado,
                usuario=self.request.user
            )
        
        # Transición a ESPERANDO_APROBACION: Calcular fecha de vencimiento si no tiene o si recién entra al estado
        if estado_anterior != OrdenTrabajo.Estado.ESPERANDO_APROBACION and orden.estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION:
            from apps.seguridad.models import Empresa
            empresa = Empresa.objects.first()
            dias = empresa.dias_validez_cotizacion if empresa else 15
            orden.fecha_vencimiento_cotizacion = timezone.now() + timezone.timedelta(days=dias)
            orden.save(update_fields=['fecha_vencimiento_cotizacion'])

    @action(detail=True, methods=['post'])
    def aprobar_servicios(self, request, pk=None):
        """Endpoint para aprobar masivamente servicios y repuestos luego que el cliente revisa."""
        orden = self.get_object()
        
        # Validar vencimiento de la cotización
        if orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion:
            return Response(
                {'error': 'La cotización ha expirado. Por favor, actualice la fecha de vencimiento en los detalles de la orden para proceder.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
                    stock_record = InventarioStock.objects.select_for_update().filter(
                        repuesto=orp.repuesto, stock_disponible__gte=orp.cantidad
                    ).first()
                    if not stock_record:
                        stock_record = InventarioStock.objects.select_for_update().filter(repuesto=orp.repuesto).first()

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

            # Liberar la reserva de los repuestos que dejan de estar aprobados en este envío.
            # Sin esto, reaprobar el mismo repuesto en una llamada posterior lo reservaría
            # una segunda vez sobre la misma cantidad física (bug detectado y corregido).
            repuestos_a_desaprobar = OrdenRepuesto.objects.filter(
                orden=orden, aprobado_cliente=True
            ).exclude(id__in=repuestos_ids)
            for orp in repuestos_a_desaprobar:
                if orp.instalado:
                    raise ValidationError(
                        f"No se puede quitar la aprobación del repuesto '{orp.repuesto.codigo}': "
                        "ya fue instalado. Revierta la instalación primero."
                    )
                stock_record = InventarioStock.objects.select_for_update().filter(repuesto=orp.repuesto).first()
                if stock_record:
                    stock_record.stock_disponible += orp.cantidad
                    stock_record.stock_reservado -= orp.cantidad
                    stock_record.save()

                    MovimientoInventario.objects.create(
                        repuesto=orp.repuesto,
                        ubicacion=stock_record.ubicacion,
                        tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                        cantidad=orp.cantidad,
                        stock_resultante=stock_record.stock_disponible,
                        motivo=f"Liberación de reserva por desaprobación en OT-{orden.numero}",
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
            raise ValidationError("Debe especificar la sucursal (sucursal_id) para enviar la orden al POS.")


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
                
            venta.total = subtotal_acumulado
            venta.subtotal, venta.igv = VentasService.descomponer_total_con_impuesto(venta.total)
            venta.save()
            
            # NOTA: Ya no cambiamos a FACTURADO aquí, se hará cuando se pague en POS.
            
        return Response({
            'status': 'ok',
            'message': 'Enviado a POS correctamente.',
            'venta_id': venta.id,
            'ticket': venta.ticket_kiosko,
            'estado_orden': orden.estado
        })

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def anular(self, request, pk=None):
        """
        Anula una orden de trabajo: libera las reservas de stock de sus repuestos
        aprobados aún no instalados y registra el motivo en el historial de estados.
        No revierte repuestos ya instalados (ese stock ya salió físicamente).
        """
        orden = self.get_object()

        if orden.estado in (OrdenTrabajo.Estado.FACTURADO, OrdenTrabajo.Estado.CANCELADO):
            raise ValidationError(f"No se puede anular una orden en estado '{orden.estado}'.")

        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).exists()
        if venta_existente:
            raise ValidationError(
                "No se puede anular: ya existe un ticket en el Punto de Venta para esta orden. "
                "Cancele ese ticket antes de anular la orden."
            )

        if orden.repuestos.filter(instalado=True).exists():
            raise ValidationError(
                "No se puede anular: esta orden tiene repuestos ya instalados (ese stock ya salió del almacén)."
            )

        motivo = (request.data.get('motivo') or '').strip()
        if not motivo:
            raise ValidationError("Debe indicar el motivo de la anulación.")

        for orp in orden.repuestos.filter(aprobado_cliente=True, instalado=False):
            stock_record = InventarioStock.objects.filter(repuesto=orp.repuesto).first()
            if stock_record:
                stock_record.stock_disponible += orp.cantidad
                stock_record.stock_reservado -= orp.cantidad
                stock_record.save()

                MovimientoInventario.objects.create(
                    repuesto=orp.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                    cantidad=orp.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Liberación de reserva por anulación de OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT_ANULACION'
                )

        orden.estado = OrdenTrabajo.Estado.CANCELADO
        orden.save(update_fields=['estado'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
            observaciones=motivo
        )

        serializer = self.get_serializer(orden)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def generar_pdf(self, request, pk=None):
        orden = self.get_object()
        
        # Calcular totales
        total_servicios = sum(s.precio_estimado for s in orden.servicios.all())
        total_repuestos = sum(r.cantidad * r.precio_unitario for r in orden.repuestos.all())
        total_general = total_servicios + total_repuestos
        
        from apps.seguridad.models import CuentaBancaria
        cuentas = CuentaBancaria.objects.filter(estado=True)
        
        # Configurar contexto
        context = {
            'orden': orden,
            'total_servicios': total_servicios,
            'total_repuestos': total_repuestos,
            'total_general': total_general,
            'cuentas_bancarias': cuentas,
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

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)

class OrdenServicioViewSet(viewsets.ModelViewSet):
    queryset = OrdenServicio.objects.all()
    serializer_class = OrdenServicioSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    def marcar_completado(self, request, pk=None):
        servicio = self.get_object()
        servicio.completado = not servicio.completado
        servicio.save(update_fields=['completado'])
        return Response({'status': 'ok', 'completado': servicio.completado})

class OrdenRepuestoViewSet(viewsets.ModelViewSet):
    queryset = OrdenRepuesto.objects.select_related('repuesto')
    serializer_class = OrdenRepuestoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    @transaction.atomic
    def marcar_instalado(self, request, pk=None):
        """
        Alterna 'instalado'. Es simétrico: activar convierte la reserva en
        salida definitiva (Kardex SALIDA); desactivar devuelve esa reserva
        (Kardex RESERVA positivo). Antes solo activar tenía efecto, así que
        activar/desactivar repetidamente descontaba stock de más (bug real
        ya detectado en producción, corregido aquí).
        """
        repuesto_orden = self.get_object()
        orden = repuesto_orden.orden

        if orden.estado in (OrdenTrabajo.Estado.CANCELADO, OrdenTrabajo.Estado.FACTURADO):
            raise ValidationError(
                f"No se puede modificar la instalación de un repuesto en una orden '{orden.estado}'."
            )

        nuevo_valor = not repuesto_orden.instalado

        if nuevo_valor and not repuesto_orden.aprobado_cliente:
            raise ValidationError("No se puede instalar un repuesto que no ha sido aprobado.")

        stock_record = InventarioStock.objects.select_for_update().filter(repuesto=repuesto_orden.repuesto).first()

        if stock_record:
            if nuevo_valor:
                # Instalar: convertir la reserva en salida definitiva.
                stock_record.stock_reservado -= repuesto_orden.cantidad
                stock_record.save()
                MovimientoInventario.objects.create(
                    repuesto=repuesto_orden.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                    cantidad=-repuesto_orden.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Instalación en OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT'
                )
            else:
                # Revertir instalación: la reserva vuelve a estar activa.
                stock_record.stock_reservado += repuesto_orden.cantidad
                stock_record.save()
                MovimientoInventario.objects.create(
                    repuesto=repuesto_orden.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                    cantidad=repuesto_orden.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Reversión de instalación en OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT'
                )

        repuesto_orden.instalado = nuevo_valor
        repuesto_orden.save(update_fields=['instalado'])

        return Response({'status': 'ok', 'instalado': repuesto_orden.instalado})

class PlantillaPreventivaViewSet(viewsets.ModelViewSet):
    queryset = PlantillaPreventiva.objects.all()
    serializer_class = PlantillaPreventivaSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]

class ConsultaVehiculoPublicaView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        placa = request.data.get('placa')
        dni = request.data.get('dni')
        
        if not placa or not dni:
            return Response({'error': 'Placa y DNI son requeridos'}, status=status.HTTP_400_BAD_REQUEST)
            
        vehiculo = Vehiculo.objects.filter(placa__iexact=placa).first()
        if not vehiculo:
            return Response({'error': 'Vehículo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
            
        # Buscar la última orden activa (que no esté FACTURADA ni CANCELADA)
        orden = OrdenTrabajo.objects.filter(vehiculo=vehiculo).exclude(estado__in=['FACTURADO', 'CANCELADO']).order_by('-fecha_ingreso').first()
        
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
            
        # Preparar resumen de la orden
        servicios = [
            {
                'descripcion': s.descripcion,
                'completado': s.completado,
                'precio': s.precio_estimado
            }
            for s in orden.servicios.filter(aprobado_cliente=True)
        ]
        
        repuestos = [
            {
                'descripcion': r.repuesto.nombre if r.repuesto else 'Repuesto',
                'instalado': r.instalado,
                'precio': r.precio_unitario,
                'cantidad': r.cantidad
            }
            for r in orden.repuestos.select_related('repuesto').filter(aprobado_cliente=True)
        ]
        
        total_estimado = sum([float(s['precio']) for s in servicios]) + sum([float(r['precio']) * float(r['cantidad']) for r in repuestos])
        
        return Response({
            'vehiculo': vehiculo_data,
            'has_active_order': True,
            'orden': {
                'numero': orden.numero,
                'estado': orden.estado,
                'fecha_ingreso': orden.fecha_ingreso,
                'servicios': servicios,
                'repuestos': repuestos,
                'total_estimado': total_estimado
            }
        })

