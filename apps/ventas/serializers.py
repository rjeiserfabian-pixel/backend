from rest_framework import serializers
from .models import (
    Caja, SesionCaja, MovimientoCaja, TipoComprobante, SerieComprobante, MetodoPago,
    Impuesto, Venta, DetalleVenta, PagoVenta, CuentaPorCobrar, CuotaCredito, PagoCuota,
    KioskoTerminal, Proforma, ProformaDetalle
)
from apps.inventario.serializers import RepuestoSerializer
from apps.clientes.serializers import ClienteSerializer
from apps.vehiculos.serializers import VehiculoSerializer
from decimal import Decimal
from .services import VentasService


# ──────────────────────────────────────────────
# CONFIGURACIONES
# ──────────────────────────────────────────────

class MetodoPagoSerializer(serializers.ModelSerializer):
    class Meta:
        model = MetodoPago
        fields = '__all__'


class ImpuestoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Impuesto
        fields = '__all__'


class TipoComprobanteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoComprobante
        fields = '__all__'


class SerieComprobanteSerializer(serializers.ModelSerializer):
    tipo_comprobante_nombre = serializers.CharField(source='tipo_comprobante.nombre', read_only=True)

    class Meta:
        model = SerieComprobante
        fields = '__all__'


class KioskoTerminalSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = KioskoTerminal
        fields = ['id', 'nombre', 'sucursal', 'sucursal_nombre', 'codigo_activacion', 'activo', 'activado_en', 'ultima_actividad', 'creado_en']
        # El token es el secreto que usa el dispositivo para identificarse; nunca
        # se expone por el listado del panel de administración, solo se entrega
        # una vez al activar el kiosko (ver KioskoActivarSerializer/acción activar).
        read_only_fields = ['codigo_activacion', 'activado_en', 'ultima_actividad', 'creado_en']


class CajaSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    almacen_nombre = serializers.CharField(source='almacen_defecto.nombre', read_only=True)

    class Meta:
        model = Caja
        fields = '__all__'


# ──────────────────────────────────────────────
# CAJA CHICA Y SESIONES
# ──────────────────────────────────────────────

class SesionCajaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.CharField(source='usuario.get_full_name', read_only=True)
    caja_nombre = serializers.CharField(source='caja.nombre', read_only=True)

    class Meta:
        model = SesionCaja
        fields = '__all__'


class MovimientoCajaSerializer(serializers.ModelSerializer):
    metodo_pago_nombre = serializers.CharField(source='metodo_pago.nombre', read_only=True)
    creado_por_nombre = serializers.CharField(source='creado_por.get_full_name', read_only=True)

    class Meta:
        model = MovimientoCaja
        fields = '__all__'
        read_only_fields = ('fecha', 'creado_por')


# ──────────────────────────────────────────────
# VENTAS Y KIOSKO
# ──────────────────────────────────────────────

class DetalleVentaSerializer(serializers.ModelSerializer):
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_unidad_medida = serializers.CharField(source='repuesto.unidad_medida.abreviatura', read_only=True)
    
    class Meta:
        model = DetalleVenta
        fields = '__all__'
        read_only_fields = ('venta',)


class PagoVentaSerializer(serializers.ModelSerializer):
    metodo_pago = serializers.CharField(source='movimiento_caja.metodo_pago.nombre', read_only=True)
    referencia = serializers.CharField(source='movimiento_caja.referencia', read_only=True)

    class Meta:
        model = PagoVenta
        fields = ('id', 'monto', 'fecha_pago', 'metodo_pago', 'referencia')


class VentaSerializer(serializers.ModelSerializer):
    detalles = DetalleVentaSerializer(many=True, read_only=True)
    pagos = PagoVentaSerializer(many=True, read_only=True)
    referencia_origen = serializers.SerializerMethodField()
    cliente_nombre = serializers.CharField(source='cliente.nombres', read_only=True)
    cliente_apellidos = serializers.CharField(source='cliente.apellidos', read_only=True)
    cliente_dni = serializers.CharField(source='cliente.dni', read_only=True)
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True)
    tipo_comprobante_nombre = serializers.CharField(source='tipo_comprobante.nombre', read_only=True)
    vendedor_nombre = serializers.CharField(source='sesion_caja.usuario.nombre_completo', read_only=True)
    caja_nombre = serializers.CharField(source='sesion_caja.caja.nombre', read_only=True)
    kiosko_nombre = serializers.CharField(source='kiosko.nombre', read_only=True)

    class Meta:
        model = Venta
        fields = '__all__'

    def get_referencia_origen(self, obj):
        if obj.ticket_kiosko:
            return obj.ticket_kiosko
        guia = getattr(obj, 'guia_remision_origen', None)
        return str(guia) if guia else ''


class ProformaDetalleSerializer(serializers.ModelSerializer):
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_unidad_medida = serializers.CharField(source='repuesto.unidad_medida.abreviatura', read_only=True)

    class Meta:
        model = ProformaDetalle
        fields = '__all__'
        read_only_fields = ('proforma', 'subtotal_linea')


class ProformaSerializer(serializers.ModelSerializer):
    detalles = ProformaDetalleSerializer(many=True)
    cliente_nombre = serializers.CharField(source='cliente.nombres', read_only=True)
    cliente_apellidos = serializers.CharField(source='cliente.apellidos', read_only=True)
    cliente_dni = serializers.CharField(source='cliente.dni', read_only=True)
    cliente_telefono = serializers.CharField(source='cliente.telefono', read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    creado_por_nombre = serializers.CharField(source='creado_por.nombre_completo', read_only=True)
    venta_generada_ticket = serializers.CharField(source='venta_generada.ticket_kiosko', read_only=True)

    class Meta:
        model = Proforma
        fields = '__all__'
        read_only_fields = (
            'numero', 'subtotal', 'igv', 'descuento_total', 'total',
            'creado_por', 'creado_en', 'actualizado_en', 'convertido_en', 'venta_generada'
        )

    def validate_detalles(self, value):
        if not value:
            raise serializers.ValidationError("La proforma debe tener al menos un item.")
        for item in value:
            tipo = item.get('tipo') or ProformaDetalle.Tipo.REPUESTO
            repuesto = item.get('repuesto')
            descripcion = (item.get('descripcion') or '').strip()
            cantidad = Decimal(str(item.get('cantidad') or 0))
            precio = Decimal(str(item.get('precio_unitario') or 0))
            descuento = Decimal(str(item.get('descuento') or 0))

            if cantidad <= 0:
                raise serializers.ValidationError("La cantidad de cada item debe ser mayor a cero.")
            if precio < 0:
                raise serializers.ValidationError("El precio unitario no puede ser negativo.")
            if descuento < 0:
                raise serializers.ValidationError("El descuento no puede ser negativo.")
            if descuento > cantidad * precio:
                raise serializers.ValidationError("El descuento no puede superar el total del item.")
            if tipo == ProformaDetalle.Tipo.REPUESTO and not repuesto:
                raise serializers.ValidationError("Los items de repuesto deben indicar un producto.")
            if tipo == ProformaDetalle.Tipo.SERVICIO and not descripcion:
                raise serializers.ValidationError("Los servicios deben tener una descripción.")
        return value

    def _generar_numero(self, sucursal):
        from django.db.models import Max

        serie = SerieDocumentoInterno.objects.filter(
            sucursal=sucursal,
            tipo_documento=SerieDocumentoInterno.TipoDocumento.PROFORMA_VENTAS,
            estado=True
        ).select_for_update().first()
        if serie:
            numero = serie.generar_siguiente_correlativo()
            serie.correlativo_actual += 1
            serie.save(update_fields=['correlativo_actual'])
            return numero

        max_id = Proforma.objects.aggregate(max_id=Max('id'))['max_id'] or 0
        return f"PROF-{str(max_id + 1).zfill(6)}"

    def _recalcular_totales(self, proforma):
        bruto = Decimal('0.00')
        descuento_items = Decimal('0.00')
        for detalle in proforma.detalles.all():
            bruto += (detalle.cantidad * detalle.precio_unitario)
            descuento_items += detalle.descuento

        descuento_global = Decimal(str(proforma.descuento_global or 0))
        if descuento_global < 0:
            descuento_global = Decimal('0.00')
        descuento_total = min(descuento_items + descuento_global, bruto)
        total = (bruto - descuento_total).quantize(Decimal('0.01'))

        if proforma.incluye_igv:
            # Los precios ingresados YA incluyen IGV → descomponemos
            subtotal, igv = VentasService.descomponer_total_con_impuesto(total)
        else:
            # Los precios ingresados NO incluyen IGV → IGV = 0, total sin cambio
            subtotal = total
            igv = Decimal('0.00')

        proforma.subtotal = subtotal
        proforma.igv = igv
        proforma.descuento_total = descuento_total.quantize(Decimal('0.01'))
        proforma.total = total
        proforma.save(update_fields=['subtotal', 'igv', 'descuento_total', 'total', 'actualizado_en'])

    def _guardar_detalles(self, proforma, detalles_data):
        proforma.detalles.all().delete()
        for item in detalles_data:
            tipo = item.get('tipo') or ProformaDetalle.Tipo.REPUESTO
            repuesto = item.get('repuesto')
            descripcion = (item.get('descripcion') or '').strip()
            if tipo == ProformaDetalle.Tipo.REPUESTO and repuesto:
                descripcion = descripcion or repuesto.nombre

            cantidad = Decimal(str(item.get('cantidad') or 0))
            precio = Decimal(str(item.get('precio_unitario') or 0))
            descuento = Decimal(str(item.get('descuento') or 0))
            subtotal_linea = ((cantidad * precio) - descuento).quantize(Decimal('0.01'))

            ProformaDetalle.objects.create(
                proforma=proforma,
                tipo=tipo,
                repuesto=repuesto if tipo == ProformaDetalle.Tipo.REPUESTO else None,
                descripcion=descripcion,
                cantidad=cantidad,
                precio_unitario=precio,
                descuento=descuento,
                subtotal_linea=subtotal_linea,
            )
        self._recalcular_totales(proforma)

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        request = self.context.get('request')
        usuario = request.user if request else validated_data.pop('creado_por')
        sucursal = validated_data['sucursal']
        validated_data['creado_por'] = usuario
        validated_data['numero'] = self._generar_numero(sucursal)
        proforma = Proforma.objects.create(**validated_data)
        self._guardar_detalles(proforma, detalles_data)
        return proforma

    def update(self, instance, validated_data):
        detalles_data = validated_data.pop('detalles', None)
        if instance.estado == Proforma.Estado.CONVERTIDA:
            raise serializers.ValidationError("No se puede editar una proforma convertida a POS.")
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if detalles_data is not None:
            self._guardar_detalles(instance, detalles_data)
        else:
            self._recalcular_totales(instance)
        return instance


class TicketKioskoCreateSerializer(serializers.Serializer):
    cliente_id = serializers.IntegerField()
    vehiculo_id = serializers.IntegerField(required=False, allow_null=True)
    # El origen real de verdad para la sucursal es kiosko_token, resuelto en el
    # backend — sucursal_id queda opcional y se ignora si hay un token válido.
    # Así un ticket no puede terminar en una sucursal distinta a la del kiosko
    # físico solo porque alguien manipule el valor enviado desde el navegador.
    sucursal_id = serializers.IntegerField(required=False, allow_null=True)
    kiosko_token = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    moneda = serializers.CharField(max_length=3, required=False, default='PEN')
    tipo_cambio = serializers.DecimalField(max_digits=10, decimal_places=4, required=False, default=1.0000)
    kilometraje = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    detalles = serializers.ListField(
        child=serializers.DictField()
    )
    # detalles format: [{"repuesto_id": 1, "cantidad": 2, "precio_unitario": 10.50}]


class ProcesarVentaSerializer(serializers.Serializer):
    tipo_comprobante = serializers.CharField(max_length=30)
    moneda = serializers.CharField(max_length=3, required=False, default='PEN')
    tipo_cambio = serializers.DecimalField(max_digits=10, decimal_places=4, required=False, default=1.0000)
    pagos = serializers.ListField(
        child=serializers.DictField()
    )
    # pagos format: [{"metodo_pago_id": 1, "monto": 50.00, "referencia": "op-123"}]
    credito = serializers.DictField(required=False)
    # credito format: {"frecuencia": "MENSUAL", "cuotas": 3}
    almacen_origen_id = serializers.IntegerField(required=False)
    detalles = serializers.ListField(child=serializers.DictField(), required=False)


# ──────────────────────────────────────────────
# CRÉDITOS Y COBRANZAS
# ──────────────────────────────────────────────

class PagoCuotaSerializer(serializers.ModelSerializer):
    cajero_nombre = serializers.CharField(source='movimiento_caja.creado_por.username', read_only=True)
    metodo_pago_nombre = serializers.CharField(source='movimiento_caja.metodo_pago.nombre', read_only=True)
    referencia = serializers.CharField(source='movimiento_caja.referencia', read_only=True)
    numero_recibo = serializers.CharField(source='movimiento_caja.numero_recibo', read_only=True)
    
    class Meta:
        model = PagoCuota
        fields = '__all__'


class CuotaCreditoSerializer(serializers.ModelSerializer):
    pagos = PagoCuotaSerializer(many=True, read_only=True)
    # El estado ATRASADA nunca se escribe en la BD (nada lo actualiza); en vez
    # de confiar en ese campo, se calcula al vuelo comparando la fecha de
    # vencimiento contra hoy, igual que ya hace Cuentas por Pagar.
    esta_vencida = serializers.SerializerMethodField()

    class Meta:
        model = CuotaCredito
        fields = '__all__'

    def get_esta_vencida(self, obj):
        from django.utils import timezone
        return obj.saldo_pendiente > 0 and obj.fecha_vencimiento < timezone.localdate()


class CuentaPorCobrarSerializer(serializers.ModelSerializer):
    cuotas = CuotaCreditoSerializer(many=True, read_only=True)
    esta_atrasada = serializers.SerializerMethodField()
    cliente_nombre = serializers.CharField(source='venta.cliente.nombres', read_only=True)
    cliente_apellidos = serializers.CharField(source='venta.cliente.apellidos', read_only=True)
    cliente_dni = serializers.CharField(source='venta.cliente.dni', read_only=True)
    cliente_telefono = serializers.CharField(source='venta.cliente.telefono', read_only=True)
    cliente_direccion = serializers.CharField(source='venta.cliente.direccion', read_only=True)
    venta_serie = serializers.CharField(source='venta.serie_correlativo', read_only=True)
    venta_fecha = serializers.DateTimeField(source='venta.creado_en', read_only=True)
    venta_detalles = DetalleVentaSerializer(source='venta.detalles', many=True, read_only=True)

    class Meta:
        model = CuentaPorCobrar
        fields = '__all__'

    def get_esta_atrasada(self, obj):
        from django.utils import timezone
        hoy = timezone.localdate()
        return any(cuota.saldo_pendiente > 0 and cuota.fecha_vencimiento < hoy for cuota in obj.cuotas.all())

from .models import SerieDocumentoInterno

class SerieDocumentoInternoSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = SerieDocumentoInterno
        fields = '__all__'
