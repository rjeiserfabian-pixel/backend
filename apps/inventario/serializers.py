from rest_framework import serializers
from .models import (
    Categoria, MarcaRepuesto, Repuesto, AplicacionRepuesto,
    Sucursal, Almacen, UbicacionFisica, InventarioStock, MovimientoInventario,
)


# ──────────────────────────────────────────────
# SERIALIZERS EXISTENTES (sin cambios)
# ──────────────────────────────────────────────

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
    aplicaciones = AplicacionRepuestoSerializer(many=True, required=False)
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    marca_nombre = serializers.CharField(source='marca.nombre', read_only=True)
    stock_total_disponible = serializers.IntegerField(read_only=True)
    stock_minimo_global = serializers.IntegerField(read_only=True)
    inventario_stock = InventarioStockResumenSerializer(many=True, read_only=True)

    class Meta:
        model = Repuesto
        fields = '__all__'

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
