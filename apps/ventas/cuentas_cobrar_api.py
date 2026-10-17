import logging
import hashlib
import json
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.seguridad.auditoria import registrar
from apps.seguridad.permissions import TienePermiso
from .creditos_anteriores import CreditoAnteriorSerializer, LineaAnteriorSerializer, guardar_credito
from .models import CuentaPorCobrar, CuotaCredito, DetalleCreditoAnterior, MetodoPago, MovimientoCaja, PagoCuota, SesionCaja
from .paginacion import VentaPagination
from .serializers import CuentaPorCobrarSerializer, PagoCuotaSerializer

logger = logging.getLogger(__name__)


class PagoEntradaSerializer(serializers.Serializer):
    metodo_pago_id = serializers.PrimaryKeyRelatedField(queryset=MetodoPago.objects.filter(estado=True))
    monto = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    referencia = serializers.CharField(max_length=100, allow_blank=True, default='')

    def validate(self, data):
        if data['metodo_pago_id'].requiere_referencia and not data['referencia'].strip():
            raise serializers.ValidationError('El método de pago requiere un número de operación o referencia.')
        return data


class CobroEntradaSerializer(serializers.Serializer):
    pagos = PagoEntradaSerializer(many=True, allow_empty=False)
    solicitud_id = serializers.UUIDField(required=False)


class CuentaPorCobrarViewSet(viewsets.ReadOnlyModelViewSet):
    """Las escrituras pasan exclusivamente por acciones con reglas de negocio."""
    serializer_class = CuentaPorCobrarSerializer
    pagination_class = VentaPagination
    queryset = CuentaPorCobrar.objects.all()

    def get_permissions(self):
        acciones = {'pagar_cuota': 'REGISTRAR_PAGO', 'registrar_anterior': 'REGISTRAR_ANTERIOR', 'editar_anterior': 'EDITAR_ANTERIOR', 'anular_anterior': 'ANULAR_ANTERIOR'}
        return [TienePermiso('CUENTAS.POR_COBRAR.' + acciones.get(self.action, 'VER'))]

    def get_queryset(self):
        qs = super().get_queryset().select_related('cliente', 'sucursal', 'vehiculo', 'venta').prefetch_related('detalles_anteriores', 'venta__detalles__repuesto__unidad_medida', 'cuotas__pagos__movimiento_caja__metodo_pago', 'cuotas__pagos__movimiento_caja__creado_por')
        params = self.request.query_params
        for param, campo in [('estado', 'estado'), ('cliente_id', 'cliente_id'), ('sucursal_id', 'sucursal_id'), ('origen', 'origen'), ('moneda', 'moneda')]:
            if params.get(param):
                qs = qs.filter(**{campo: params[param]})
        if params.get('placa'):
            qs = qs.filter(Q(placa_original__icontains=params['placa']) | Q(vehiculo__placa__icontains=params['placa']))
        if params.get('search'):
            s = params['search'].strip()
            qs = qs.filter(Q(codigo_credito__icontains=s) | Q(documento_origen__icontains=s) | Q(cliente__nombres__icontains=s) | Q(cliente__apellidos__icontains=s) | Q(cliente__dni__icontains=s) | Q(vehiculo__placa__icontains=s))
        if params.get('vencida') == 'true':
            qs = qs.filter(Exists(CuotaCredito.objects.filter(cuenta_cobrar_id=OuterRef('pk'), saldo_pendiente__gt=0, fecha_vencimiento__lt=timezone.localdate()))).exclude(estado='ANULADO')
        return qs.order_by('-creado_en', '-pk')

    def list(self, request, *args, **kwargs):
        totales = list(self.get_queryset().exclude(estado='ANULADO').order_by().values('moneda').annotate(saldo_pendiente=Sum('saldo_pendiente'), monto_financiado=Sum('monto_financiado')))
        response = super().list(request, *args, **kwargs)
        if isinstance(response.data, dict):
            response.data['totales_por_moneda'] = totales
        return response

    @action(detail=False, methods=['get'], url_path='resumen-clientes')
    def resumen_clientes(self, request):
        vencidas = CuotaCredito.objects.filter(cuenta_cobrar_id=OuterRef('pk'), saldo_pendiente__gt=0, fecha_vencimiento__lt=timezone.localdate())
        qs = self.get_queryset().exclude(estado='ANULADO').annotate(tiene_vencida=Exists(vencidas)).order_by().values('cliente_id', 'cliente__dni', 'cliente__nombres', 'cliente__apellidos', 'moneda').annotate(total_deuda=Sum('monto_financiado'), saldo_pendiente_total=Sum('saldo_pendiente'), tiene_atrasos=Count('pk', filter=Q(tiene_vencida=True))).order_by('-saldo_pendiente_total', 'cliente_id', 'moneda')
        page = self.paginate_queryset(qs)
        rows = list(page if page is not None else qs)
        for row in rows:
            # Alias conservados para consumidores anteriores del resumen.
            for campo in ('dni', 'nombres', 'apellidos'):
                row['venta__cliente__' + campo] = row['cliente__' + campo]
            row['venta__cliente__id'] = row['cliente_id']
        return self.get_paginated_response(rows) if page is not None else Response(rows)

    @action(detail=False, methods=['get'], url_path='cuotas-vencidas')
    def cuotas_vencidas(self, request):
        hoy = timezone.localdate()
        qs = CuotaCredito.objects.select_related('cuenta_cobrar__cliente').filter(fecha_vencimiento__lt=hoy, saldo_pendiente__gt=0).exclude(cuenta_cobrar__estado='ANULADO')
        if request.query_params.get('sucursal_id'):
            qs = qs.filter(cuenta_cobrar__sucursal_id=request.query_params['sucursal_id'])
        data = [{'id': c.id, 'cuenta_cobrar_id': c.cuenta_cobrar_id, 'cliente_nombre': str(c.cuenta_cobrar.cliente).split(' - ', 1)[-1], 'codigo_credito': c.cuenta_cobrar.codigo_credito, 'numero_cuota': c.numero_cuota, 'saldo_pendiente': c.saldo_pendiente, 'moneda': c.cuenta_cobrar.moneda, 'fecha_vencimiento': c.fecha_vencimiento, 'dias_vencido': (hoy - c.fecha_vencimiento).days} for c in qs.order_by('fecha_vencimiento', 'pk')[:30]]
        return Response({'total': qs.count(), 'results': data})

    @action(detail=False, methods=['post'], url_path='registrar-anterior')
    def registrar_anterior(self, request):
        huella = hashlib.sha256(json.dumps(request.data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        # Validar UUID antes de consultarlo y permitir reintento de la misma solicitud.
        token = serializers.UUIDField(required=False).run_validation(request.data['solicitud_id']) if request.data.get('solicitud_id') else None
        if token:
            anterior = CuentaPorCobrar.objects.filter(solicitud_id=token).first()
            if anterior:
                if anterior.solicitud_huella != huella or anterior.creado_por_id != request.user.pk:
                    return Response({'error': 'La solicitud ya fue utilizada para otro registro.'}, status=409)
                return Response(self.get_serializer(anterior).data)
        entrada = CreditoAnteriorSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                cuenta = guardar_credito(dict(entrada.validated_data), request.user)
                cuenta.solicitud_huella = huella
                cuenta.save(update_fields=['solicitud_huella'])
                registrar(request, 'CUENTAS', 'REGISTRAR_ANTERIOR', 'ventas_cuenta_cobrar', cuenta.pk, despues=self.get_serializer(cuenta).data)
        except IntegrityError:
            return Response({'error': 'El documento o la solicitud ya están registrados. Actualice la lista antes de volver a intentar.'}, status=409)
        return Response(self.get_serializer(cuenta).data, status=201)

    @action(detail=True, methods=['post'], url_path='editar-anterior')
    @transaction.atomic
    def editar_anterior(self, request, pk=None):
        cuenta = get_object_or_404(CuentaPorCobrar.objects.select_for_update(), pk=pk, origen='CREDITO_ANTERIOR')
        if cuenta.estado == 'ANULADO':
            return Response({'error': 'El crédito está anulado.'}, status=400)
        motivo = str(request.data.get('motivo', '')).strip()
        if not motivo:
            return Response({'error': 'Indique el motivo de la corrección.'}, status=400)
        antes = self.get_serializer(cuenta).data
        if PagoCuota.objects.filter(cuota__cuenta_cobrar=cuenta).exists():
            permitidos = {'concepto', 'observaciones', 'motivo'}
            if cuenta.sin_detalle:
                permitidos |= {'detalles', 'sin_detalle'}
            if set(request.data) - permitidos:
                return Response({'error': 'Con cobros registrados solo se permite corregir concepto y observaciones.'}, status=400)
            if 'detalles' in request.data:
                lineas = LineaAnteriorSerializer(data=request.data['detalles'], many=True, allow_empty=False)
                lineas.is_valid(raise_exception=True)
                if sum((d['subtotal_linea'] for d in lineas.validated_data), Decimal('0')) + cuenta.ajuste_documento != cuenta.importe_original or (cuenta.sin_vehiculo and any(d['tipo'] == 'SERVICIO' for d in lineas.validated_data)):
                    return Response({'error': 'El detalle debe respetar el importe y la asociación de vehículo originales.'}, status=400)
                DetalleCreditoAnterior.objects.bulk_create([DetalleCreditoAnterior(cuenta=cuenta, **d) for d in lineas.validated_data])
                cuenta.sin_detalle = False
            for campo in ('concepto', 'observaciones'):
                if campo in request.data:
                    valor = serializers.CharField(max_length=5000, allow_blank=campo == 'observaciones').run_validation(request.data[campo])
                    setattr(cuenta, campo, valor)
            cuenta.save()
        else:
            entrada = CreditoAnteriorSerializer(data=request.data, context={'cuenta': cuenta})
            entrada.is_valid(raise_exception=True)
            try:
                with transaction.atomic():
                    cuenta = guardar_credito(dict(entrada.validated_data), request.user, cuenta)
            except IntegrityError:
                return Response({'error': 'El documento ya está registrado.'}, status=409)
        registrar(request, 'CUENTAS', 'EDITAR_ANTERIOR', 'ventas_cuenta_cobrar', cuenta.pk, antes=antes, despues={'motivo': motivo, 'cuenta': self.get_serializer(cuenta).data})
        return Response(self.get_serializer(cuenta).data)

    @action(detail=True, methods=['post'], url_path='anular-anterior')
    @transaction.atomic
    def anular_anterior(self, request, pk=None):
        cuenta = get_object_or_404(CuentaPorCobrar.objects.select_for_update(), pk=pk, origen='CREDITO_ANTERIOR')
        motivo = serializers.CharField(max_length=1000).run_validation(request.data.get('motivo', ''))
        if PagoCuota.objects.filter(cuota__cuenta_cobrar=cuenta).exists():
            return Response({'error': 'No puede anular un crédito que ya tiene cobros.'}, status=400)
        antes = self.get_serializer(cuenta).data
        cuenta.estado = 'ANULADO'
        cuenta.motivo_anulacion = motivo
        cuenta.anulado_en = timezone.now()
        cuenta.save()
        registrar(request, 'CUENTAS', 'ANULAR_ANTERIOR', 'ventas_cuenta_cobrar', cuenta.pk, antes=antes, despues=self.get_serializer(cuenta).data)
        return Response(self.get_serializer(cuenta).data)

    @action(detail=False, methods=['post'], url_path='pagar-cuota/(?P<cuota_id>[^/.]+)')
    def pagar_cuota(self, request, cuota_id=None):
        try:
            with transaction.atomic():
                return self._pagar_cuota(request, cuota_id)
        except IntegrityError:
            return Response({'error': 'La solicitud de cobro ya está registrada. Consulte el historial antes de reintentar.'}, status=409)

    def _pagar_cuota(self, request, cuota_id):
        entrada = CobroEntradaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        original = get_object_or_404(CuotaCredito, pk=cuota_id)
        cuenta = get_object_or_404(CuentaPorCobrar.objects.select_for_update(), pk=original.cuenta_cobrar_id)
        cuota = CuotaCredito.objects.select_for_update().get(pk=original.pk)
        token = entrada.validated_data.get('solicitud_id')
        if token:
            anteriores = list(PagoCuota.objects.filter(solicitud_id=token).select_related('movimiento_caja'))
            if anteriores:
                anteriores = list(PagoCuota.objects.filter(operacion_id=anteriores[0].operacion_id).select_related('movimiento_caja'))
                esperado = [(p['metodo_pago_id'].pk, p['monto'], p['referencia']) for p in entrada.validated_data['pagos']]
                registrado = [(p.movimiento_caja.metodo_pago_id, p.monto, p.movimiento_caja.referencia or '') for p in sorted(anteriores, key=lambda p: p.pk)]
                if any(p.cuota_id != cuota.pk or p.movimiento_caja.creado_por_id != request.user.pk for p in anteriores) or esperado != registrado:
                    return Response({'error': 'La solicitud de cobro ya fue utilizada.'}, status=409)
                return Response({'message': 'Pago ya registrado.', 'pagos': PagoCuotaSerializer(anteriores, many=True).data, 'cuenta': self.get_serializer(cuenta).data})
        if cuenta.estado == 'ANULADO' or cuota.saldo_pendiente <= 0:
            return Response({'error': 'El crédito está anulado o la cuota ya está pagada.'}, status=400)
        sesion = SesionCaja.objects.select_for_update().filter(usuario=request.user, estado=SesionCaja.Estado.ABIERTA, fecha_cierre__isnull=True).order_by('pk').first()
        if not sesion:
            return Response({'error': 'Debes abrir una caja antes de registrar un cobro.'}, status=400)
        pagos = entrada.validated_data['pagos']
        total = sum((p['monto'] for p in pagos), Decimal('0'))
        if total > cuota.saldo_pendiente or total > cuenta.saldo_pendiente:
            return Response({'error': 'El monto pagado supera el saldo pendiente.'}, status=400)
        import uuid
        operacion = uuid.uuid4()
        cuota.saldo_pendiente -= total
        cuota.estado = 'PAGADA' if cuota.saldo_pendiente == 0 else 'PARCIAL'
        if cuota.saldo_pendiente == 0:
            cuota.fecha_pago = timezone.localdate()
        cuota.save()
        cuenta.saldo_pendiente -= total
        cuenta.estado = 'PAGADO' if cuenta.saldo_pendiente == 0 else 'PENDIENTE'
        cuenta.save()
        datos_cuenta = self.get_serializer(cuenta).data
        instantanea = {'cliente_nombre': datos_cuenta['cliente_nombre'], 'cliente_apellidos': datos_cuenta['cliente_apellidos'], 'cliente_dni': datos_cuenta['cliente_dni'], 'cliente_direccion': datos_cuenta.get('cliente_direccion'), 'codigo_credito': cuenta.codigo_credito, 'placa': datos_cuenta['placa'], 'documento': datos_cuenta['documento'], 'moneda': cuenta.moneda, 'fecha_original': str(cuenta.fecha_original or cuenta.venta.creado_en.date()), 'monto_financiado': str(cuenta.monto_financiado), 'saldo_pendiente': str(cuenta.saldo_pendiente), 'cuota_estado': cuota.estado, 'cuota_saldo': str(cuota.saldo_pendiente), 'numero_cuota': cuota.numero_cuota, 'fecha_vencimiento': str(cuota.fecha_vencimiento)}
        nuevos = []
        for i, p in enumerate(pagos):
            movimiento = MovimientoCaja.objects.create(sesion=sesion, tipo='INGRESO', concepto='COBRO_CUOTA', metodo_pago=p['metodo_pago_id'], monto=p['monto'], referencia=p['referencia'], referencia_origen=cuenta.codigo_credito, origen_movimiento='COBRO', creado_por=request.user)
            nuevos.append(PagoCuota.objects.create(cuota=cuota, movimiento_caja=movimiento, operacion_id=operacion, solicitud_id=token if i == 0 else None, monto=p['monto'], recibo_datos=instantanea))
        return Response({'message': 'Pago registrado exitosamente.', 'pagos': PagoCuotaSerializer(nuevos, many=True).data, 'cuenta': datos_cuenta})
