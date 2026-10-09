from django.utils import timezone
from rest_framework import serializers

from .models import AsignacionHerramienta, CategoriaHerramienta, Herramienta, HistorialHerramienta


class CategoriaHerramientaSerializer(serializers.ModelSerializer):
    total_herramientas = serializers.SerializerMethodField()

    class Meta:
        model = CategoriaHerramienta
        fields = ['id', 'nombre', 'descripcion', 'estado', 'total_herramientas']
        read_only_fields = ['id', 'total_herramientas']

    def get_total_herramientas(self, obj):
        # Se anota en el queryset del ViewSet para evitar N+1.
        return getattr(obj, 'total_herramientas_anotado', None)

    def validate_nombre(self, value):
        value = value.strip()
        qs = CategoriaHerramienta.objects.filter(nombre__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Ya existe una categoría con ese nombre.')
        return value


class HerramientaSerializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    almacen_nombre = serializers.CharField(source='almacen.nombre', read_only=True, default=None)
    proveedor_nombre = serializers.CharField(
        source='proveedor.nombre_o_razon_social', read_only=True, default=None
    )
    estado_operativo_display = serializers.CharField(
        source='get_estado_operativo_display', read_only=True
    )
    estado_fisico_display = serializers.CharField(source='get_estado_fisico_display', read_only=True)

    class Meta:
        model = Herramienta
        fields = [
            'id', 'codigo', 'nombre', 'categoria', 'categoria_nombre',
            'marca', 'modelo', 'numero_serie',
            'sucursal', 'sucursal_nombre', 'almacen', 'almacen_nombre', 'ubicacion',
            'fecha_compra', 'costo_adquisicion', 'proveedor', 'proveedor_nombre', 'garantia_hasta',
            'estado_operativo', 'estado_operativo_display',
            'estado_fisico', 'estado_fisico_display',
            'potencia', 'voltaje', 'observaciones', 'horas_uso_acumuladas',
            'estado', 'fecha_creacion', 'fecha_actualizacion',
        ]
        # El estado operativo y las horas solo cambian por acciones dedicadas
        # (cambiar-estado ahora; asignaciones y mantenimientos en fases siguientes).
        read_only_fields = [
            'id', 'codigo', 'estado_operativo', 'horas_uso_acumuladas',
            'estado', 'fecha_creacion', 'fecha_actualizacion',
        ]

    def validate_costo_adquisicion(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('El costo no puede ser negativo.')
        return value

    def validate_categoria(self, value):
        if not value.estado:
            raise serializers.ValidationError('La categoría está inactiva.')
        return value

    def validate(self, attrs):
        sucursal = attrs.get('sucursal', getattr(self.instance, 'sucursal', None))
        almacen = attrs.get('almacen', getattr(self.instance, 'almacen', None))
        if almacen and sucursal and almacen.sucursal_id != sucursal.pk:
            raise serializers.ValidationError(
                {'almacen': 'El almacén no pertenece a la sucursal seleccionada.'}
            )
        fecha_compra = attrs.get('fecha_compra', getattr(self.instance, 'fecha_compra', None))
        garantia = attrs.get('garantia_hasta', getattr(self.instance, 'garantia_hasta', None))
        if fecha_compra and garantia and garantia < fecha_compra:
            raise serializers.ValidationError(
                {'garantia_hasta': 'La garantía no puede vencer antes de la fecha de compra.'}
            )
        return attrs


class CambiarEstadoSerializer(serializers.Serializer):
    estado_operativo = serializers.ChoiceField(choices=Herramienta.EstadoOperativo.choices)
    motivo = serializers.CharField(max_length=255)


class HistorialHerramientaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()
    accion_display = serializers.CharField(source='get_accion_display', read_only=True)

    class Meta:
        model = HistorialHerramienta
        fields = ['id', 'accion', 'accion_display', 'detalle', 'usuario_nombre', 'fecha']

    def get_usuario_nombre(self, obj):
        u = obj.usuario
        return f'{u.nombres} {u.apellidos}'.strip() if u else None


def _nombre_completo(usuario):
    return f'{usuario.nombres} {usuario.apellidos}'.strip() if usuario else None


class AsignacionSerializer(serializers.ModelSerializer):
    herramienta_codigo = serializers.CharField(source='herramienta.codigo', read_only=True)
    herramienta_nombre = serializers.CharField(source='herramienta.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='herramienta.sucursal.nombre', read_only=True)
    tecnico_nombre = serializers.SerializerMethodField()
    entregado_por_nombre = serializers.SerializerMethodField()
    recibido_por_nombre = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    vencida = serializers.SerializerMethodField()

    class Meta:
        model = AsignacionHerramienta
        fields = [
            'id', 'herramienta', 'herramienta_codigo', 'herramienta_nombre', 'sucursal_nombre',
            'tecnico', 'tecnico_nombre', 'entregado_por_nombre', 'recibido_por_nombre',
            'estado', 'estado_display', 'vencida',
            'fecha_entrega', 'fecha_devolucion_esperada', 'fecha_devolucion_real',
            'estado_fisico_entrega', 'estado_fisico_devolucion', 'horas_uso_periodo',
            'observaciones_entrega', 'observaciones_devolucion', 'motivo_anulacion',
        ]
        read_only_fields = fields

    def get_tecnico_nombre(self, obj):
        return _nombre_completo(obj.tecnico)

    def get_entregado_por_nombre(self, obj):
        return _nombre_completo(obj.entregado_por)

    def get_recibido_por_nombre(self, obj):
        return _nombre_completo(obj.recibido_por)

    def get_vencida(self, obj):
        return (
            obj.estado == AsignacionHerramienta.Estado.ACTIVA
            and obj.fecha_devolucion_esperada is not None
            and obj.fecha_devolucion_esperada < timezone.localdate()
        )


class EntregarSerializer(serializers.Serializer):
    herramienta = serializers.IntegerField(min_value=1)
    tecnico = serializers.UUIDField()
    fecha_devolucion_esperada = serializers.DateField(required=False, allow_null=True)
    observaciones = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')
    # Entregar a pesar de un mantenimiento vencido (requiere permiso y motivo).
    forzar = serializers.BooleanField(required=False, default=False)
    motivo_excepcion = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')

    def validate(self, attrs):
        if attrs.get('forzar') and not attrs.get('motivo_excepcion', '').strip():
            raise serializers.ValidationError({'motivo_excepcion': 'Indique el motivo de la excepción.'})
        return attrs

    def validate_fecha_devolucion_esperada(self, value):
        if value is not None and value < timezone.localdate():
            raise serializers.ValidationError('La fecha de devolución no puede ser anterior a hoy.')
        return value


class DevolverSerializer(serializers.Serializer):
    estado_fisico_devolucion = serializers.ChoiceField(choices=Herramienta.EstadoFisico.choices)
    horas_uso_periodo = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, max_value=100000, required=False, default=0
    )
    requiere_mantenimiento = serializers.BooleanField(required=False, default=False)
    observaciones = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')


class AnularSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=255)
