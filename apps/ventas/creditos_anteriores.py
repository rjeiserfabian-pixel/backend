"""Alta y corrección de saldos anteriores; nunca procesa ventas ni inventario."""
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.clientes.models import Cliente
from apps.inventario.models import Repuesto, Sucursal
from apps.vehiculos.models import Vehiculo
from .models import CuentaPorCobrar, CuotaCredito, DetalleCreditoAnterior, SerieDocumentoInterno


class LineaAnteriorSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleCreditoAnterior
        fields = ('tipo', 'repuesto', 'codigo_original', 'descripcion', 'unidad', 'cantidad', 'precio_unitario', 'descuento')
        extra_kwargs = {'repuesto': {'queryset': Repuesto.objects.all()}, 'descuento': {'default': Decimal('0')}}

    def validate(self, data):
        bruto = data['cantidad'] * data['precio_unitario']
        if data['cantidad'] <= 0 or data['precio_unitario'] < 0 or not 0 <= data['descuento'] <= bruto:
            raise serializers.ValidationError('Cantidad, precio o descuento inválido.')
        if data['tipo'] == 'SERVICIO' and data.get('repuesto'):
            raise serializers.ValidationError('Una línea de servicio no puede tener un repuesto asociado.')
        data['subtotal_linea'] = (bruto - data['descuento']).quantize(Decimal('0.01'))
        return data


class CuotaAnteriorSerializer(serializers.Serializer):
    monto = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    fecha_vencimiento = serializers.DateField()


class CreditoAnteriorSerializer(serializers.Serializer):
    cliente = serializers.PrimaryKeyRelatedField(queryset=Cliente.objects.filter(estado=True))
    sucursal = serializers.PrimaryKeyRelatedField(queryset=Sucursal.objects.all())
    vehiculo = serializers.PrimaryKeyRelatedField(queryset=Vehiculo.objects.filter(estado=True), allow_null=True, required=False)
    sin_vehiculo = serializers.BooleanField(default=False)
    sistema_origen = serializers.CharField(max_length=100)
    documento_origen = serializers.CharField(max_length=100)
    tipo_documento_origen = serializers.CharField(max_length=50, allow_blank=True, default='')
    concepto = serializers.CharField(max_length=3000)
    observaciones = serializers.CharField(max_length=5000, allow_blank=True, default='')
    sin_detalle = serializers.BooleanField(default=False)
    moneda = serializers.ChoiceField(choices=['PEN'], default='PEN')
    fecha_original = serializers.DateField()
    fecha_corte = serializers.DateField()
    importe_original = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    abonos_anteriores = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0'), default=Decimal('0'))
    ajuste_documento = serializers.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    motivo_ajuste = serializers.CharField(max_length=255, allow_blank=True, default='')
    detalles = LineaAnteriorSerializer(many=True, default=list)
    cronograma = CuotaAnteriorSerializer(many=True, allow_empty=False)
    solicitud_id = serializers.UUIDField(required=False)
    motivo = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, data):
        for key in ('sistema_origen', 'documento_origen'):
            data[key] = ' '.join(data[key].upper().split())
        if data['fecha_original'] > data['fecha_corte'] or data['fecha_corte'] > timezone.localdate():
            raise serializers.ValidationError('La fecha original debe ser anterior o igual al corte, y el corte no puede ser futuro.')
        vehiculo = data.get('vehiculo')
        if data['sin_vehiculo']:
            if vehiculo or any(d['tipo'] == 'SERVICIO' for d in data['detalles']):
                raise serializers.ValidationError('Sin vehículo solo corresponde a repuestos sin unidad asociada.')
        elif not vehiculo or not vehiculo.clientes.filter(pk=data['cliente'].pk).exists():
            raise serializers.ValidationError('Seleccione una placa vinculada al cliente.')
        if data['sin_detalle']:
            if data['detalles'] or not data['observaciones'].strip():
                raise serializers.ValidationError('Sin desglose debe indicar sustento en observaciones y dejar el detalle vacío.')
        elif not data['detalles']:
            raise serializers.ValidationError('Agregue servicios o repuestos, o indique Sin detalle desglosado.')
        if data['ajuste_documento'] and not data['motivo_ajuste'].strip():
            raise serializers.ValidationError('Explique el ajuste del documento.')
        if not data['sin_detalle'] and sum((d['subtotal_linea'] for d in data['detalles']), Decimal('0')) + data['ajuste_documento'] != data['importe_original']:
            raise serializers.ValidationError('El detalle más el ajuste debe coincidir con el importe original.')
        saldo = data['importe_original'] - data['abonos_anteriores']
        if saldo <= 0 or sum((c['monto'] for c in data['cronograma']), Decimal('0')) != saldo:
            raise serializers.ValidationError('El saldo debe ser positivo y coincidir con la suma de cuotas pendientes.')
        if any(c['fecha_vencimiento'] < data['fecha_original'] for c in data['cronograma']):
            raise serializers.ValidationError('El vencimiento no puede ser anterior al documento original.')
        qs = CuentaPorCobrar.objects.filter(origen='CREDITO_ANTERIOR', sucursal=data['sucursal'], cliente=data['cliente'], sistema_origen=data['sistema_origen'], documento_origen=data['documento_origen'])
        if self.context.get('cuenta'):
            qs = qs.exclude(pk=self.context['cuenta'].pk)
        if qs.exists():
            raise serializers.ValidationError('Este documento anterior ya está registrado para el cliente y sucursal.')
        return data


@transaction.atomic
def guardar_credito(data, usuario, cuenta=None):
    detalles = data.pop('detalles')
    cronograma = data.pop('cronograma')
    data.pop('motivo', None)
    if cuenta:
        data.pop('solicitud_id', None)
        for campo, valor in data.items():
            setattr(cuenta, campo, valor)
    else:
        serie = SerieDocumentoInterno.objects.select_for_update().filter(sucursal=data['sucursal'], tipo_documento='CREDITO', estado=True).first()
        codigo = f"CA-{uuid.uuid4().hex.upper()}"
        if serie:
            codigo = serie.generar_siguiente_correlativo()
            serie.correlativo_actual += 1
            serie.save(update_fields=['correlativo_actual'])
        cuenta = CuentaPorCobrar(origen='CREDITO_ANTERIOR', codigo_credito=codigo, creado_por=usuario, frecuencia_pago='MENSUAL', **data)
    cuenta.placa_original = cuenta.vehiculo.placa if cuenta.vehiculo_id else ''
    cuenta.monto_financiado = cuenta.importe_original - cuenta.abonos_anteriores
    cuenta.saldo_pendiente = cuenta.monto_financiado
    cuenta.save()
    cuenta.detalles_anteriores.all().delete()
    cuenta.cuotas.all().delete()
    DetalleCreditoAnterior.objects.bulk_create([DetalleCreditoAnterior(cuenta=cuenta, **d) for d in detalles])
    CuotaCredito.objects.bulk_create([CuotaCredito(cuenta_cobrar=cuenta, numero_cuota=i, saldo_pendiente=c['monto'], **c) for i, c in enumerate(cronograma, 1)])
    return cuenta
