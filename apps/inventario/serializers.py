from rest_framework import serializers
from decimal import Decimal
from .models import (
    UnidadMedida, Categoria, MarcaRepuesto, Repuesto, AplicacionRepuesto,
    Sucursal, Almacen, UbicacionFisica, InventarioStock, MovimientoInventario,
    TrasladoInventario, TrasladoInventarioDetalle, GuiaRemision, GuiaRemisionDetalle
)


# ──────────────────────────────────────────────
# SERIALIZERS EXISTENTES (sin cambios)
# ──────────────────────────────────────────────

class UnidadMedidaSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnidadMedida
        fields = '__all__'


class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = '__all__'


class MarcaRepuestoSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarcaRepuesto
        fields = '__all__'


class AplicacionRepuestoSerializer(serializers.ModelSerializer):
    class Meta:
        model = AplicacionRepuesto
        exclude = ('repuesto',)  # Se excluye porque se asociará al crear el repuesto


class InventarioStockResumenSerializer(serializers.ModelSerializer):
    """
    Serializer ligero para mostrar el desglose de stock de un repuesto
    anidado dentro de RepuestoDetalleSerializer.
    Evita N+1 usando prefetch_related en la vista.
    """
    ubicacion_codigo = serializers.CharField(source='ubicacion.codigo', read_only=True)
    almacen_nombre = serializers.CharField(source='ubicacion.almacen.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='ubicacion.almacen.sucursal.nombre', read_only=True)
    stock_fisico_total = serializers.ReadOnlyField()
    ubicacion_detalle = serializers.SerializerMethodField()

    class Meta:
        model = InventarioStock
        fields = [
            'id', 'ubicacion', 'ubicacion_codigo', 'almacen_nombre', 'sucursal_nombre',
            'stock_disponible', 'stock_reservado', 'stock_merma', 'stock_fisico_total',
            'stock_minimo', 'ubicacion_detalle',
        ]

    def get_ubicacion_detalle(self, obj):
        partes = []
        if obj.ubicacion.pasillo and obj.ubicacion.pasillo != "-" and obj.ubicacion.pasillo.lower() != "x": 
            partes.append(f"Pasillo {obj.ubicacion.pasillo}")
        if obj.ubicacion.estante and obj.ubicacion.estante != "-" and obj.ubicacion.estante.lower() != "x": 
            partes.append(f"Estante {obj.ubicacion.estante}")
        if obj.ubicacion.casillero and obj.ubicacion.casillero != "-" and obj.ubicacion.casillero.lower() != "x": 
            partes.append(f"Casillero {obj.ubicacion.casillero}")
        return " - ".join(partes) if partes else obj.ubicacion.codigo


class RepuestoSerializer(serializers.ModelSerializer):
    codigo_barra = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    aplicaciones = AplicacionRepuestoSerializer(many=True, required=False)
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    marca_nombre = serializers.CharField(source='marca.nombre', read_only=True)
    unidad_medida_nombre = serializers.CharField(source='unidad_medida.nombre', read_only=True)
    unidad_medida_abreviatura = serializers.CharField(source='unidad_medida.abreviatura', read_only=True)
    unidad_medida_permite_decimales = serializers.BooleanField(source='unidad_medida.permite_decimales', read_only=True)
    # ReadOnlyField (no IntegerField): stock_disponible es DecimalField y se
    # vende por fracciones (ej. litros de aceite) — IntegerField truncaba los
    # decimales (8.50 -> 8), ocultando el stock real disponible.
    stock_total_disponible = serializers.ReadOnlyField()
    stock_minimo_global = serializers.ReadOnlyField()
    inventario_stock = InventarioStockResumenSerializer(many=True, read_only=True)

    class Meta:
        model = Repuesto
        fields = '__all__'

    def validate_codigo_barra(self, value):
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        qs = Repuesto.objects.filter(codigo_barra__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Ya existe un repuesto registrado con este codigo de barras.')
        return value

    def create(self, validated_data):
        aplicaciones_data = validated_data.pop('aplicaciones', [])
        repuesto = Repuesto.objects.create(**validated_data)

        for app_data in aplicaciones_data:
            AplicacionRepuesto.objects.create(repuesto=repuesto, **app_data)

        return repuesto

    def update(self, instance, validated_data):
        aplicaciones_data = validated_data.pop('aplicaciones', None)

        # Update repuesto fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Si se envían aplicaciones, reemplazamos las existentes
        if aplicaciones_data is not None:
            instance.aplicaciones.all().delete()
            for app_data in aplicaciones_data:
                AplicacionRepuesto.objects.create(repuesto=instance, **app_data)

        return instance


# ──────────────────────────────────────────────
# NUEVOS SERIALIZERS: ESTRUCTURA FÍSICA
# ──────────────────────────────────────────────

class SucursalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sucursal
        fields = '__all__'


class AlmacenSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = Almacen
        fields = '__all__'


class UbicacionFisicaSerializer(serializers.ModelSerializer):
    almacen_nombre = serializers.CharField(source='almacen.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='almacen.sucursal.nombre', read_only=True)

    class Meta:
        model = UbicacionFisica
        fields = '__all__'


# ──────────────────────────────────────────────
# NUEVOS SERIALIZERS: STOCK Y KARDEX
# ──────────────────────────────────────────────

class InventarioStockSerializer(serializers.ModelSerializer):
    """Serializer completo para ver/crear/actualizar el stock en una ubicación."""
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    repuesto_unidad = serializers.CharField(source='repuesto.unidad_medida.abreviatura', read_only=True, default='-')
    ubicacion_codigo = serializers.CharField(source='ubicacion.codigo', read_only=True)
    almacen_nombre = serializers.CharField(source='ubicacion.almacen.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='ubicacion.almacen.sucursal.nombre', read_only=True)
    stock_fisico_total = serializers.ReadOnlyField()
    ubicacion_detalle = serializers.SerializerMethodField()

    class Meta:
        model = InventarioStock
        fields = '__all__'

    def get_ubicacion_detalle(self, obj):
        partes = []
        if obj.ubicacion.pasillo and obj.ubicacion.pasillo != "-" and obj.ubicacion.pasillo.lower() != "x": 
            partes.append(f"Pasillo {obj.ubicacion.pasillo}")
        if obj.ubicacion.estante and obj.ubicacion.estante != "-" and obj.ubicacion.estante.lower() != "x": 
            partes.append(f"Estante {obj.ubicacion.estante}")
        if obj.ubicacion.casillero and obj.ubicacion.casillero != "-" and obj.ubicacion.casillero.lower() != "x": 
            partes.append(f"Casillero {obj.ubicacion.casillero}")
        return " - ".join(partes) if partes else obj.ubicacion.codigo


class RepuestoDetalleSerializer(serializers.ModelSerializer):
    """
    Serializer extendido que incluye el desglose de inventario por ubicación.
    Se usa en el endpoint de detalle (/repuestos/{id}/).
    """
    aplicaciones = AplicacionRepuestoSerializer(many=True, read_only=True)
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    marca_nombre = serializers.CharField(source='marca.nombre', read_only=True)
    unidad_medida_nombre = serializers.CharField(source='unidad_medida.nombre', read_only=True)
    unidad_medida_abreviatura = serializers.CharField(source='unidad_medida.abreviatura', read_only=True)
    unidad_medida_permite_decimales = serializers.BooleanField(source='unidad_medida.permite_decimales', read_only=True)
    inventario = InventarioStockResumenSerializer(many=True, read_only=True, source='inventario_stock')
    stock_total_disponible = serializers.ReadOnlyField()
    stock_total_fisico = serializers.ReadOnlyField()

    class Meta:
        model = Repuesto
        fields = '__all__'


class MovimientoInventarioSerializer(serializers.ModelSerializer):
    """Serializer de solo lectura para el Kardex. El Kardex es inmutable."""
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    ubicacion_codigo = serializers.CharField(source='ubicacion.codigo', read_only=True)
    usuario_nombre = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoInventario
        # El kardex es solo lectura: no se expone para escritura
        fields = [
            'id', 'repuesto', 'repuesto_codigo', 'repuesto_nombre',
            'ubicacion', 'ubicacion_codigo',
            'tipo_movimiento', 'cantidad', 'stock_resultante',
            'motivo', 'usuario', 'usuario_nombre',
            'referencia_id', 'referencia_tipo', 'fecha',
        ]
        read_only_fields = fields  # Todos los campos son de solo lectura

    def get_usuario_nombre(self, obj):
        if obj.usuario:
            return getattr(obj.usuario, 'get_full_name', lambda: str(obj.usuario))()
        return 'Sistema'


class TrasladoInventarioDetalleSerializer(serializers.ModelSerializer):
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_unidad = serializers.CharField(source='repuesto.unidad_medida.abreviatura', read_only=True, default='-')
    ubicacion_origen_nombre = serializers.CharField(source='ubicacion_origen.codigo', read_only=True)
    ubicacion_destino_nombre = serializers.CharField(source='ubicacion_destino.codigo', read_only=True)

    class Meta:
        model = TrasladoInventarioDetalle
        fields = '__all__'
        read_only_fields = ('traslado',)


class TrasladoInventarioSerializer(serializers.ModelSerializer):
    detalles = TrasladoInventarioDetalleSerializer(many=True, read_only=True)
    almacen_origen_nombre = serializers.CharField(source='almacen_origen.nombre', read_only=True)
    almacen_destino_nombre = serializers.CharField(source='almacen_destino.nombre', read_only=True)
    usuario_nombre = serializers.SerializerMethodField()
    confirmado_por_nombre = serializers.SerializerMethodField()
    serie_prefijo = serializers.CharField(source='serie.prefijo', read_only=True, default=None)
    numero_documento = serializers.SerializerMethodField()

    # Write only fields for creation
    detalles_datos = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=True
    )

    class Meta:
        model = TrasladoInventario
        fields = [
            'id', 'fecha_traslado', 'almacen_origen', 'almacen_origen_nombre',
            'almacen_destino', 'almacen_destino_nombre', 'observaciones',
            'usuario', 'usuario_nombre', 'estado', 'confirmado_por', 'confirmado_por_nombre',
            'fecha_confirmacion', 'motivo_rechazo', 'serie_prefijo', 'correlativo',
            'numero_documento', 'detalles', 'detalles_datos'
        ]
        read_only_fields = ('usuario', 'estado', 'confirmado_por', 'fecha_confirmacion', 'motivo_rechazo', 'correlativo')

    def get_numero_documento(self, obj):
        if obj.serie and obj.correlativo:
            return f"{obj.serie.prefijo}-{str(obj.correlativo).zfill(obj.serie.longitud_correlativo)}"
        return f"TR-{str(obj.id).zfill(6)}"

    def get_confirmado_por_nombre(self, obj):
        if obj.confirmado_por:
            return getattr(obj.confirmado_por, 'get_full_name', lambda: str(obj.confirmado_por))()
        return None

    def get_usuario_nombre(self, obj):
        if obj.usuario:
            return getattr(obj.usuario, 'get_full_name', lambda: str(obj.usuario))()
        return 'Sistema'

# ──────────────────────────────────────────────
# SERIALIZERS NUEVOS: GUÍAS DE REMISIÓN
# ──────────────────────────────────────────────

class GuiaRemisionDetalleSerializer(serializers.ModelSerializer):
    repuesto_codigo = serializers.CharField(source='repuesto.codigo', read_only=True)
    repuesto_nombre = serializers.CharField(source='repuesto.nombre', read_only=True)
    repuesto_unidad = serializers.CharField(source='repuesto.unidad_medida.nombre', read_only=True, default='-')

    class Meta:
        model = GuiaRemisionDetalle
        fields = ['id', 'repuesto', 'repuesto_codigo', 'repuesto_nombre', 'repuesto_unidad', 'cantidad']

class GuiaRemisionSerializer(serializers.ModelSerializer):
    detalles = GuiaRemisionDetalleSerializer(many=True, read_only=True)
    cliente_nombre = serializers.SerializerMethodField()
    almacen_origen_nombre = serializers.CharField(source='almacen_origen.nombre', read_only=True)
    ubigeo_partida_nombre = serializers.CharField(source='ubigeo_partida.nombre', read_only=True)
    ubigeo_llegada_nombre = serializers.CharField(source='ubigeo_llegada.nombre', read_only=True)
    transportista_nombre = serializers.SerializerMethodField()
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True)
    serie_prefijo = serializers.CharField(source='serie.prefijo', read_only=True)
    numero_documento = serializers.SerializerMethodField()
    total_facturable = serializers.SerializerMethodField()
    entregado_por_nombre = serializers.SerializerMethodField()
    venta_generada_serie = serializers.CharField(source='venta_generada.serie_correlativo', read_only=True)

    # Write-only para creación
    detalles_datos = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False
    )

    class Meta:
        model = GuiaRemision
        fields = [
            'id', 'sucursal', 'almacen_origen', 'almacen_origen_nombre', 'serie', 'serie_prefijo',
            'correlativo', 'numero_documento', 'total_facturable', 'fecha_emision', 'fecha_traslado',
            'cliente', 'cliente_nombre', 'ubigeo_partida', 'ubigeo_partida_nombre', 'punto_partida',
            'ubigeo_llegada', 'ubigeo_llegada_nombre', 'punto_llegada', 'motivo_traslado', 'observaciones',
            'transportista', 'transportista_nombre', 'vehiculo', 'vehiculo_placa', 'estado',
            'fecha_salida', 'fecha_entrega', 'entregado_por', 'entregado_por_nombre',
            'recibido_por', 'observacion_entrega', 'venta_generada', 'venta_generada_serie',
            'detalles', 'detalles_datos'
        ]
        read_only_fields = (
            'fecha_emision', 'estado', 'fecha_salida', 'fecha_entrega',
            'entregado_por', 'venta_generada'
        )

    def get_numero_documento(self, obj):
        if obj.serie and obj.correlativo:
            return f"{obj.serie.prefijo}-{str(obj.correlativo).zfill(obj.serie.longitud_correlativo)}"
        return f"GR-{str(obj.id).zfill(6)}" if obj.id else None

    def get_total_facturable(self, obj):
        total = Decimal('0.00')
        for detalle in obj.detalles.all():
            precio = Decimal(str(detalle.repuesto.precio_lista or 0))
            cantidad = Decimal(str(detalle.cantidad))
            total += precio * cantidad
        return total.quantize(Decimal('0.01'))

    def get_cliente_nombre(self, obj):
        if obj.cliente:
            return f"{obj.cliente.nombres} {obj.cliente.apellidos}".strip()
        return '-'

    def get_transportista_nombre(self, obj):
        if obj.transportista:
            return obj.transportista.nombre_o_razon_social
        return '-'

    def get_entregado_por_nombre(self, obj):
        if obj.entregado_por:
            return getattr(obj.entregado_por, 'nombre_completo', str(obj.entregado_por))
        return None
