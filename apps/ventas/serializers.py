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
    tiene_qr = serializers.SerializerMethodField()
    qr_url = serializers.SerializerMethodField()

    class Meta:
        model = MetodoPago
        # La imagen del QR se sube/quita por su propio endpoint (POST/DELETE .../qr/).
        exclude = ['qr_imagen']

    def get_tiene_qr(self, obj):
        return bool(obj.qr_imagen)

    def get_qr_url(self, obj):
        # Ruta relativa: una URL absoluta apuntaría a 127.0.0.1 y no abriría desde otras PCs.
        return f'/media/{obj.qr_imagen.name}' if obj.qr_imagen else None


class QRMetodoPagoSerializer(serializers.Serializer):
    """Subida del QR de cobro de un método de pago (PNG/JPG/WEBP, máx. 2 MB)."""
    imagen = serializers.ImageField(required=False)
    descripcion = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate_imagen(self, imagen):
        if imagen.size > 2 * 1024 * 1024:
            raise serializers.ValidationError('La imagen no puede superar los 2 MB.')
        if getattr(getattr(imagen, 'image', None), 'format', None) not in ('PNG', 'JPEG', 'WEBP'):
            raise serializers.ValidationError('Use una imagen PNG, JPG o WEBP.')
        return imagen


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
    repuesto_stock = serializers.DecimalField(source='repuesto.stock_total_disponible', max_digits=12, decimal_places=2, read_only=True, default=0)
    repuesto_tipo_igv_codigo = serializers.CharField(source='repuesto.tipo_igv.codigo_sunat', read_only=True, default=None)
    descripcion = serializers.CharField(required=False, allow_blank=True, default='')

    class Meta:
        model = ProformaDetalle
        fields = '__all__'
        read_only_fields = ('proforma', 'subtotal_linea', 'base_imponible', 'igv_linea')


class ProformaSerializer(serializers.ModelSerializer):
    detalles = ProformaDetalleSerializer(many=True)
    cliente_nombre = serializers.CharField(source='cliente.nombres', read_only=True)
    cliente_apellidos = serializers.CharField(source='cliente.apellidos', read_only=True)
    cliente_dni = serializers.CharField(source='cliente.dni', read_only=True)
    cliente_telefono = serializers.CharField(source='cliente.telefono', read_only=True)
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True, default=None)
    vehiculo_marca = serializers.CharField(source='vehiculo.marca', read_only=True, default=None)
    vehiculo_modelo = serializers.CharField(source='vehiculo.modelo', read_only=True, default=None)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    creado_por_nombre = serializers.CharField(source='creado_por.nombre_completo', read_only=True)
    venta_generada_ticket = serializers.CharField(source='venta_generada.ticket_kiosko', read_only=True)

    class Meta:
        model = Proforma
        fields = '__all__'
        read_only_fields = (
            'numero', 'subtotal', 'igv', 'descuento_total', 'total',
            'total_gravado', 'total_exonerado', 'total_inafecto',
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
            tipo_afectacion = item.get('tipo_afectacion')

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
            if tipo_afectacion and tipo_afectacion not in ProformaDetalle.TipoAfectacion.values:
                raise serializers.ValidationError(f"Tipo de afectación '{tipo_afectacion}' no válido.")
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
        suma_base_gravada = Decimal('0.00')
        suma_base_exonerada = Decimal('0.00')
        suma_base_inafecta = Decimal('0.00')
        descuento_items = Decimal('0.00')

        detalles = list(proforma.detalles.all())
        for detalle in detalles:
            descuento_items += detalle.descuento
            if detalle.tipo_afectacion == ProformaDetalle.TipoAfectacion.GRAVADO_18:
                suma_base_gravada += detalle.base_imponible
            elif detalle.tipo_afectacion == ProformaDetalle.TipoAfectacion.EXONERADO:
                suma_base_exonerada += detalle.base_imponible
            elif detalle.tipo_afectacion == ProformaDetalle.TipoAfectacion.INAFECTO:
                suma_base_inafecta += detalle.base_imponible

        suma_base_total = suma_base_gravada + suma_base_exonerada + suma_base_inafecta
        descuento_global = Decimal(str(proforma.descuento_global or 0))
        if descuento_global < 0:
            descuento_global = Decimal('0.00')
        descuento_global = min(descuento_global, suma_base_total)

        if descuento_global > 0 and suma_base_total > 0:
            factor_gravado = suma_base_gravada / suma_base_total
            factor_exonerado = suma_base_exonerada / suma_base_total

            desc_gravado = (descuento_global * factor_gravado).quantize(Decimal('0.01'))
            desc_exonerado = (descuento_global * factor_exonerado).quantize(Decimal('0.01'))
            desc_inafecto = (descuento_global - desc_gravado - desc_exonerado).quantize(Decimal('0.01'))

            total_gravado = max(Decimal('0.00'), suma_base_gravada - desc_gravado)
            total_exonerado = max(Decimal('0.00'), suma_base_exonerada - desc_exonerado)
            total_inafecto = max(Decimal('0.00'), suma_base_inafecta - desc_inafecto)
        else:
            total_gravado = suma_base_gravada
            total_exonerado = suma_base_exonerada
            total_inafecto = suma_base_inafecta

        tasa_decimal = VentasService.obtener_tasa_impuesto() / Decimal('100')
        if descuento_global > 0:
            igv = (total_gravado * tasa_decimal).quantize(Decimal('0.01'))
        else:
            igv = sum(d.igv_linea for d in detalles).quantize(Decimal('0.01'))

        subtotal = (total_gravado + total_exonerado + total_inafecto).quantize(Decimal('0.01'))
        total = (subtotal + igv).quantize(Decimal('0.01'))
        descuento_total = (descuento_items + descuento_global).quantize(Decimal('0.01'))

        proforma.total_gravado = total_gravado
        proforma.total_exonerado = total_exonerado
        proforma.total_inafecto = total_inafecto
        proforma.subtotal = subtotal
        proforma.igv = igv
        proforma.descuento_total = descuento_total
        proforma.total = total
        proforma.save(update_fields=[
            'total_gravado', 'total_exonerado', 'total_inafecto',
            'subtotal', 'igv', 'descuento_total', 'total', 'actualizado_en'
        ])

    def _guardar_detalles(self, proforma, detalles_data):
        proforma.detalles.all().delete()
        tasa_decimal = VentasService.obtener_tasa_impuesto() / Decimal('100')
        factor = Decimal('1') + tasa_decimal

        for item in detalles_data:
            tipo = item.get('tipo') or ProformaDetalle.Tipo.REPUESTO
            repuesto = item.get('repuesto')
            descripcion = (item.get('descripcion') or '').strip()
            if tipo == ProformaDetalle.Tipo.REPUESTO and repuesto:
                descripcion = descripcion or repuesto.nombre

            cantidad = Decimal(str(item.get('cantidad') or 0))
            precio = Decimal(str(item.get('precio_unitario') or 0))
            descuento = Decimal(str(item.get('descuento') or 0))

            tipo_afectacion = item.get('tipo_afectacion')
            if not tipo_afectacion:
                if repuesto and getattr(repuesto, 'tipo_igv', None):
                    cod = str(repuesto.tipo_igv.codigo_sunat or '').strip()
                    if cod == '10':
                        tipo_afectacion = ProformaDetalle.TipoAfectacion.GRAVADO_18
                    elif cod == '30':
                        tipo_afectacion = ProformaDetalle.TipoAfectacion.INAFECTO
                    else:
                        tipo_afectacion = ProformaDetalle.TipoAfectacion.EXONERADO
                else:
                    tipo_afectacion = ProformaDetalle.TipoAfectacion.EXONERADO

            precio_incluye_igv = item.get('precio_incluye_igv')
            if precio_incluye_igv is None:
                precio_incluye_igv = proforma.incluye_igv

            if tipo_afectacion == ProformaDetalle.TipoAfectacion.GRAVADO_18:
                if precio_incluye_igv:
                    # El precio unitario ingresado incluye IGV (18%)
                    importe_con_igv = max(Decimal('0.00'), (cantidad * precio) - descuento)
                    base_imponible = (importe_con_igv / factor).quantize(Decimal('0.01'))
                    igv_linea = (importe_con_igv - base_imponible).quantize(Decimal('0.01'))
                    subtotal_linea = importe_con_igv.quantize(Decimal('0.01'))
                else:
                    # El precio unitario ingresado es valor neto sin IGV
                    base_imponible = max(Decimal('0.00'), (cantidad * precio) - descuento).quantize(Decimal('0.01'))
                    igv_linea = (base_imponible * tasa_decimal).quantize(Decimal('0.01'))
                    subtotal_linea = (base_imponible + igv_linea).quantize(Decimal('0.01'))
            else:
                # EXONERADO o INAFECTO: sin IGV
                base_imponible = max(Decimal('0.00'), (cantidad * precio) - descuento).quantize(Decimal('0.01'))
                igv_linea = Decimal('0.00')
                subtotal_linea = base_imponible

            ProformaDetalle.objects.create(
                proforma=proforma,
                tipo=tipo,
                repuesto=repuesto if tipo == ProformaDetalle.Tipo.REPUESTO else None,
                descripcion=descripcion,
                cantidad=cantidad,
                precio_unitario=precio,
                descuento=descuento,
                tipo_afectacion=tipo_afectacion,
                precio_incluye_igv=precio_incluye_igv,
                base_imponible=base_imponible,
                igv_linea=igv_linea,
                subtotal_linea=subtotal_linea,
            )
        self._recalcular_totales(proforma)

    def _actualizar_kilometraje_vehiculo(self, vehiculo, kilometraje):
        if vehiculo and kilometraje is not None:
            if vehiculo.kilometraje_actual is None or kilometraje >= vehiculo.kilometraje_actual:
                vehiculo.kilometraje_actual = kilometraje
                vehiculo.save(update_fields=['kilometraje_actual'])

    def create(self, validated_data):
        detalles_data = validated_data.pop('detalles')
        request = self.context.get('request')
        usuario = request.user if request else validated_data.pop('creado_por')
        sucursal = validated_data['sucursal']
        validated_data['creado_por'] = usuario
        validated_data['numero'] = self._generar_numero(sucursal)
        validated_data.setdefault('total_gravado', Decimal('0.00'))
        validated_data.setdefault('total_exonerado', Decimal('0.00'))
        validated_data.setdefault('total_inafecto', Decimal('0.00'))
        proforma = Proforma.objects.create(**validated_data)
        self._guardar_detalles(proforma, detalles_data)
        self._actualizar_kilometraje_vehiculo(proforma.vehiculo, proforma.kilometraje)
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
        self._actualizar_kilometraje_vehiculo(instance.vehiculo, instance.kilometraje)
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


class DetalleCreditoAnteriorLecturaSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import DetalleCreditoAnterior
        model = DetalleCreditoAnterior
        fields = '__all__'


class CuentaPorCobrarSerializer(serializers.ModelSerializer):
    detalles_anteriores = DetalleCreditoAnteriorLecturaSerializer(many=True, read_only=True)
    placa = serializers.SerializerMethodField()
    documento = serializers.SerializerMethodField()
    cuotas = CuotaCreditoSerializer(many=True, read_only=True)
    esta_atrasada = serializers.SerializerMethodField()
    cliente_nombre = serializers.CharField(source='cliente.nombres', read_only=True)
    cliente_apellidos = serializers.CharField(source='cliente.apellidos', read_only=True)
    cliente_dni = serializers.CharField(source='cliente.dni', read_only=True)
    cliente_telefono = serializers.CharField(source='cliente.telefono', read_only=True)
    cliente_direccion = serializers.CharField(source='cliente.direccion', read_only=True)
    venta_serie = serializers.CharField(source='venta.serie_correlativo', read_only=True)
    venta_fecha = serializers.DateTimeField(source='venta.creado_en', read_only=True)
    venta_detalles = DetalleVentaSerializer(source='venta.detalles', many=True, read_only=True)

    class Meta:
        model = CuentaPorCobrar
        fields = '__all__'

    def get_esta_atrasada(self, obj):
        from django.utils import timezone
        hoy = timezone.localdate()
        return obj.estado != 'ANULADO' and any(cuota.saldo_pendiente > 0 and cuota.fecha_vencimiento < hoy for cuota in obj.cuotas.all())

    def get_placa(self, obj):
        return obj.placa_original or (obj.vehiculo.placa if obj.vehiculo_id else '')

    def get_documento(self, obj):
        return obj.documento_origen if obj.origen == 'CREDITO_ANTERIOR' else obj.venta.serie_correlativo

from .models import SerieDocumentoInterno

class SerieDocumentoInternoSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = SerieDocumentoInterno
        fields = '__all__'
