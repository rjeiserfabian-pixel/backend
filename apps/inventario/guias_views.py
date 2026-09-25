from rest_framework import pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from apps.seguridad.permissions import TienePermiso
from .models import (
    Almacen, GuiaRemision, GuiaRemisionDetalle, InventarioStock,
    MovimientoInventario, Repuesto
)
from .serializers import GuiaRemisionSerializer
from apps.ventas.models import (
    DetalleVenta, MetodoPago, MovimientoCaja, PagoVenta, SerieComprobante,
    SesionCaja, TipoComprobante, Venta
)
from apps.ventas.serializers import VentaSerializer
from apps.ventas.services import VentasService


class GuiaRemisionPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class GuiaRemisionViewSet(viewsets.ModelViewSet):
    queryset = (
        GuiaRemision.objects
        .select_related(
            'sucursal', 'almacen_origen', 'serie', 'cliente', 'ubigeo_partida',
            'ubigeo_llegada', 'transportista', 'vehiculo', 'entregado_por',
            'venta_generada'
        )
        .prefetch_related('detalles__repuesto__unidad_medida')
        .order_by('-id')
    )
    serializer_class = GuiaRemisionSerializer
    pagination_class = GuiaRemisionPagination

    def get_permissions(self):
        if self.action == 'facturar':
            return [TienePermiso("VENTAS.POS.CREAR")]
        if self.action in ('preparar_salida', 'dar_salida', 'completar_traslado'):
            return [TienePermiso("INVENTARIO.TRASLADOS.APROBAR")]
        if self.request.method == 'GET':
            return [TienePermiso("INVENTARIO.TRASLADOS.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("INVENTARIO.TRASLADOS.APROBAR")]
        return [TienePermiso("INVENTARIO.TRASLADOS.CREAR")]

    def get_queryset(self):
        queryset = super().get_queryset()
        estado = self.request.query_params.get('estado')
        sucursal = self.request.query_params.get('sucursal')
        cliente = self.request.query_params.get('cliente')
        if estado:
            queryset = queryset.filter(estado=estado)
        if sucursal:
            queryset = queryset.filter(sucursal_id=sucursal)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        return queryset

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        detalles_datos = serializer.validated_data.pop('detalles_datos', [])
        if not detalles_datos:
            return Response({'detail': 'Debe agregar al menos un producto a la guia.'}, status=status.HTTP_400_BAD_REQUEST)

        sucursal = serializer.validated_data['sucursal']
        almacen_origen = serializer.validated_data.get('almacen_origen')
        if almacen_origen and almacen_origen.sucursal_id != sucursal.id:
            return Response({'detail': 'El almacen de origen no pertenece a la sucursal de la guia.'}, status=status.HTTP_400_BAD_REQUEST)
        if not almacen_origen:
            almacen_origen = Almacen.objects.filter(sucursal=sucursal, estado=True).order_by('id').first()
            if not almacen_origen:
                return Response({'detail': 'La sucursal no tiene un almacen activo para usar como origen.'}, status=status.HTTP_400_BAD_REQUEST)
            serializer.validated_data['almacen_origen'] = almacen_origen

        serie = serializer.validated_data.get('serie')
        if serie:
            serie = type(serie).objects.select_for_update().get(id=serie.id)
            correlativo = serie.correlativo_actual + 1
            serie.correlativo_actual = correlativo
            serie.save(update_fields=['correlativo_actual'])
        else:
            correlativo = 0

        guia = serializer.save(correlativo=correlativo)

        for det in detalles_datos:
            repuesto = Repuesto.objects.get(id=det['repuesto_id'])
            GuiaRemisionDetalle.objects.create(
                guia=guia,
                repuesto=repuesto,
                cantidad=det['cantidad']
            )

        headers = self.get_success_headers(serializer.data)
        response_serializer = self.get_serializer(guia)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        guia = self.get_object()
        if guia.estado not in (GuiaRemision.Estado.CREADA, GuiaRemision.Estado.LISTA_PARA_SALIDA):
            return Response(
                {'detail': 'Solo se puede eliminar una guia antes de dar salida al traslado.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='preparar-salida')
    @transaction.atomic
    def preparar_salida(self, request, pk=None):
        guia = GuiaRemision.objects.select_for_update().get(pk=self.get_object().pk)
        if guia.estado not in (GuiaRemision.Estado.CREADA, GuiaRemision.Estado.LISTA_PARA_SALIDA):
            return Response(
                {"detail": "Solo se puede preparar una guia creada o lista para salida."},
                status=status.HTTP_400_BAD_REQUEST
            )

        transportista_id = request.data.get('transportista_id')
        vehiculo_id = request.data.get('vehiculo_id')
        if not transportista_id or not vehiculo_id:
            return Response(
                {"detail": "Debe especificar un transportista y un vehiculo."},
                status=status.HTTP_400_BAD_REQUEST
            )

        from apps.clientes.models import Transportista
        from apps.vehiculos.models import VehiculoTransporte

        guia.transportista = Transportista.objects.get(id=transportista_id)
        guia.vehiculo = VehiculoTransporte.objects.get(id=vehiculo_id)
        guia.estado = GuiaRemision.Estado.LISTA_PARA_SALIDA
        guia.save(update_fields=['transportista', 'vehiculo', 'estado'])

        response_serializer = self.get_serializer(guia)
        return Response(response_serializer.data)

    @action(detail=True, methods=['post'], url_path='facturar')
    @transaction.atomic
    def facturar(self, request, pk=None):
        guia = GuiaRemision.objects.select_for_update().get(pk=self.get_object().pk)
        if guia.estado != GuiaRemision.Estado.COMPLETADA:
            return Response(
                {"detail": "Solo se puede facturar una guia completada."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if guia.venta_generada_id:
            return Response(
                {"detail": "Esta guia ya tiene una venta generada."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not guia.cliente_id:
            return Response(
                {"detail": "La guia debe tener cliente para generar la venta."},
                status=status.HTTP_400_BAD_REQUEST
            )

        tipo_comprobante_id = request.data.get('tipo_comprobante_id')
        serie_id = request.data.get('serie_id')
        pagos_data = request.data.get('pagos') or []

        if not pagos_data and request.data.get('metodo_pago_id'):
            pagos_data = [{
                'metodo_pago_id': request.data.get('metodo_pago_id'),
                'monto': request.data.get('monto'),
                'referencia': request.data.get('referencia')
            }]

        if not tipo_comprobante_id or not serie_id or not pagos_data:
            return Response(
                {"detail": "Debe indicar tipo de comprobante, serie y al menos un metodo de pago."},
                status=status.HTTP_400_BAD_REQUEST
            )

        tipo_comprobante = TipoComprobante.objects.get(id=tipo_comprobante_id, estado=True)
        serie = SerieComprobante.objects.select_for_update().filter(
            id=serie_id,
            sucursal=guia.sucursal,
            tipo_comprobante=tipo_comprobante,
            estado=True
        ).first()
        if not serie:
            return Response(
                {"detail": "La serie seleccionada no corresponde al tipo de comprobante o sucursal de la guia."},
                status=status.HTTP_400_BAD_REQUEST
            )

        sesion = (
            SesionCaja.objects
            .select_related('caja')
            .filter(usuario=request.user, estado=SesionCaja.Estado.ABIERTA)
            .first()
        )
        if not sesion:
            return Response(
                {"detail": "Debes tener una sesion de caja abierta para facturar la guia como venta pagada."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if sesion.caja.sucursal_id != guia.sucursal_id:
            return Response(
                {"detail": "La sesion de caja abierta pertenece a otra sucursal."},
                status=status.HTTP_400_BAD_REQUEST
            )

        detalles = list(guia.detalles.select_related('repuesto', 'repuesto__tipo_igv'))
        if not detalles:
            return Response(
                {"detail": "La guia no tiene productos para facturar."},
                status=status.HTTP_400_BAD_REQUEST
            )

        total = Decimal('0.00')
        lineas = []
        for detalle in detalles:
            precio_unitario = Decimal(str(detalle.repuesto.precio_lista or 0)).quantize(Decimal('0.01'))
            cantidad = Decimal(str(detalle.cantidad))
            subtotal_linea = (cantidad * precio_unitario).quantize(Decimal('0.01'))
            if subtotal_linea <= 0:
                return Response(
                    {"detail": f"El producto {detalle.repuesto.codigo} no tiene un precio valido para facturar."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            total += subtotal_linea
            lineas.append((detalle, precio_unitario, subtotal_linea))

        try:
            pagos_caja, monto_recibido, vuelto = VentasService.preparar_pagos_para_caja(pagos_data, total)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        correlativo = serie.generar_siguiente_correlativo()
        serie.correlativo_actual += 1
        serie.save(update_fields=['correlativo_actual'])

        subtotal, igv = VentasService.descomponer_total_con_impuesto(total)
        ahora = timezone.now()
        numero_guia = str(guia)
        venta = Venta.objects.create(
            cliente=guia.cliente,
            sucursal=guia.sucursal,
            sesion_caja=sesion,
            estado=Venta.Estado.PAGADA,
            tipo_comprobante=tipo_comprobante,
            serie_correlativo=correlativo,
            ticket_kiosko=numero_guia,
            subtotal=subtotal,
            igv=igv,
            total=total,
            fecha_emision=ahora,
            creado_en=ahora,
            monto_recibido=monto_recibido,
            vuelto=vuelto,
        )

        for detalle, precio_unitario, subtotal_linea in lineas:
            DetalleVenta.objects.create(
                venta=venta,
                repuesto=detalle.repuesto,
                almacen_origen=guia.almacen_origen,
                cantidad=detalle.cantidad,
                precio_unitario=precio_unitario,
                costo_unitario=detalle.repuesto.precio_compra,
                impuesto_aplicado=detalle.repuesto.tipo_igv,
                subtotal_linea=subtotal_linea,
            )

        for pago in pagos_caja:
            movimiento = MovimientoCaja.objects.create(
                sesion=sesion,
                tipo=MovimientoCaja.Tipo.INGRESO,
                concepto=MovimientoCaja.Concepto.VENTA,
                metodo_pago=pago['metodo_pago'],
                monto=pago['monto_caja'],
                referencia=pago['referencia'] or numero_guia,
                origen_movimiento=MovimientoCaja.OrigenMovimiento.VENTA,
                venta_origen=venta,
                creado_por=request.user
            )
            PagoVenta.objects.create(
                venta=venta,
                movimiento_caja=movimiento,
                monto=pago['monto_caja']
            )

        guia.venta_generada = venta
        guia.estado = GuiaRemision.Estado.FACTURADA
        guia.save(update_fields=['venta_generada', 'estado'])

        return Response({
            'message': 'Guia facturada correctamente.',
            'guia': self.get_serializer(guia).data,
            'venta': VentaSerializer(venta).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def dar_salida(self, request, pk=None):
        guia = GuiaRemision.objects.select_for_update().get(pk=self.get_object().pk)
        if guia.estado not in (GuiaRemision.Estado.CREADA, GuiaRemision.Estado.LISTA_PARA_SALIDA):
            return Response(
                {"detail": "La guia debe estar en estado CREADA o LISTA_PARA_SALIDA para dar salida."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if guia.estado == GuiaRemision.Estado.CREADA:
            transportista_id = request.data.get('transportista_id')
            vehiculo_id = request.data.get('vehiculo_id')
            if transportista_id and vehiculo_id:
                from apps.clientes.models import Transportista
                from apps.vehiculos.models import VehiculoTransporte
                guia.transportista = Transportista.objects.get(id=transportista_id)
                guia.vehiculo = VehiculoTransporte.objects.get(id=vehiculo_id)

        if not guia.transportista_id or not guia.vehiculo_id:
            return Response(
                {"detail": "La guia debe tener transportista y vehiculo antes de dar salida."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not guia.almacen_origen_id:
            return Response(
                {"detail": "La guia no tiene almacen de origen configurado."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            self._descontar_stock_por_guia(guia, request.user)
        except ValueError as exc:
            transaction.set_rollback(True)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        guia.estado = GuiaRemision.Estado.EN_TRASLADO
        guia.fecha_salida = timezone.now()
        guia.save(update_fields=['transportista', 'vehiculo', 'estado', 'fecha_salida'])

        response_serializer = self.get_serializer(guia)
        return Response(response_serializer.data)

    def _descontar_stock_por_guia(self, guia, usuario):
        detalles = list(guia.detalles.select_related('repuesto'))
        if not detalles:
            raise ValueError('La guia no tiene productos para trasladar.')

        for detalle in detalles:
            stocks = (
                InventarioStock.objects
                .select_for_update()
                .filter(
                    repuesto=detalle.repuesto,
                    ubicacion__almacen=guia.almacen_origen,
                    stock_disponible__gt=0
                )
                .select_related('ubicacion')
                .order_by('ubicacion__codigo')
            )

            cantidad_restante = detalle.cantidad
            for stock in stocks:
                if cantidad_restante <= 0:
                    break

                descontar = min(stock.stock_disponible, cantidad_restante)
                stock.stock_disponible -= descontar
                stock.save(update_fields=['stock_disponible'])

                MovimientoInventario.objects.create(
                    repuesto=detalle.repuesto,
                    ubicacion=stock.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                    cantidad=-descontar,
                    stock_resultante=stock.stock_disponible,
                    motivo=f"Guia de Remision {guia}",
                    usuario=usuario,
                    referencia_id=guia.id,
                    referencia_tipo='GUIA_REMISION'
                )
                cantidad_restante -= descontar

            if cantidad_restante > 0:
                raise ValueError(
                    f"Stock insuficiente para {detalle.repuesto.codigo} en el almacen "
                    f"{guia.almacen_origen.nombre}. Faltan {cantidad_restante} unidades."
                )

    @action(detail=True, methods=['post'], url_path='completar')
    @transaction.atomic
    def completar_traslado(self, request, pk=None):
        guia = GuiaRemision.objects.select_for_update().get(pk=self.get_object().pk)
        if guia.estado != GuiaRemision.Estado.EN_TRASLADO:
            return Response(
                {"detail": "La guia debe estar en estado EN_TRASLADO para completar."},
                status=status.HTTP_400_BAD_REQUEST
            )

        recibido_por = (request.data.get('recibido_por') or '').strip()
        observacion_entrega = (request.data.get('observacion_entrega') or '').strip()
        if not recibido_por:
            return Response(
                {"detail": "Debe indicar quien recibio el traslado."},
                status=status.HTTP_400_BAD_REQUEST
            )

        guia.estado = GuiaRemision.Estado.COMPLETADA
        guia.fecha_entrega = timezone.now()
        guia.entregado_por = request.user
        guia.recibido_por = recibido_por
        guia.observacion_entrega = observacion_entrega
        guia.save(update_fields=[
            'estado', 'fecha_entrega', 'entregado_por', 'recibido_por', 'observacion_entrega'
        ])

        response_serializer = self.get_serializer(guia)
        return Response(response_serializer.data)
