from django.utils import timezone
from rest_framework import serializers

from .models import (
    Herramienta,
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
    RegistroMantenimiento,
)

ESTADOS_SIN_PLAN = {
    Herramienta.EstadoOperativo.PERDIDA,
    Herramienta.EstadoOperativo.DADA_DE_BAJA,
}


def _nombre(usuario):
    return f'{usuario.nombres} {usuario.apellidos}'.strip() if usuario else None


class PlanMantenimientoSerializer(serializers.ModelSerializer):
    herramienta_codigo = serializers.CharField(source='herramienta.codigo', read_only=True)
    herramienta_nombre = serializers.CharField(source='herramienta.nombre', read_only=True)
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    horas_actuales = serializers.DecimalField(
        source='herramienta.horas_uso_acumuladas', max_digits=10, decimal_places=2, read_only=True
    )
    vencimiento = serializers.SerializerMethodField()
    dias_restantes = serializers.SerializerMethodField()
    horas_restantes = serializers.SerializerMethodField()

    class Meta:
        model = PlanMantenimientoHerramienta
        fields = [
            'id', 'herramienta', 'herramienta_codigo', 'herramienta_nombre',
            'nombre', 'tipo', 'tipo_display', 'intervalo_dias', 'intervalo_horas',
            'ultima_fecha', 'ultimas_horas', 'proxima_fecha', 'proximas_horas',
            'horas_actuales', 'dias_restantes', 'horas_restantes', 'vencimiento', 'activo',
        ]
        read_only_fields = [
            'id', 'ultimas_horas', 'proxima_fecha', 'proximas_horas', 'horas_actuales',
        ]

    def get_vencimiento(self, obj):
        return obj.estado_vencimiento() if obj.activo else 'INACTIVO'

    def get_dias_restantes(self, obj):
        if not obj.proxima_fecha:
            return None
        return (obj.proxima_fecha - timezone.localdate()).days

    def get_horas_restantes(self, obj):
        if obj.proximas_horas is None:
            return None
        return obj.proximas_horas - obj.herramienta.horas_uso_acumuladas

    def validate_herramienta(self, value):
        if self.instance and value.pk != self.instance.herramienta_id:
            raise serializers.ValidationError('No se puede cambiar la herramienta de un plan.')
        if not value.estado or value.estado_operativo in ESTADOS_SIN_PLAN:
            raise serializers.ValidationError('La herramienta está dada de baja o perdida.')
        return value

    def validate_nombre(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('El nombre es obligatorio.')
        return value

    def validate_ultima_fecha(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('La fecha del último mantenimiento no puede ser futura.')
        return value

    def validate(self, attrs):
        dias = attrs.get('intervalo_dias', getattr(self.instance, 'intervalo_dias', None))
        horas = attrs.get('intervalo_horas', getattr(self.instance, 'intervalo_horas', None))
        if not dias and not horas:
            raise serializers.ValidationError(
                {'intervalo_dias': 'Indique un intervalo en días, en horas de uso, o ambos.'}
            )
        if horas is not None and horas <= 0:
            raise serializers.ValidationError({'intervalo_horas': 'Debe ser mayor que cero.'})
        # 0 o vacío significa "sin intervalo" para el campo en días.
        if 'intervalo_dias' in attrs and not attrs['intervalo_dias']:
            attrs['intervalo_dias'] = None
        return attrs


class RegistroMantenimientoSerializer(serializers.ModelSerializer):
    herramienta_codigo = serializers.CharField(source='herramienta.codigo', read_only=True)
    herramienta_nombre = serializers.CharField(source='herramienta.nombre', read_only=True)
    plan_nombre = serializers.CharField(source='plan.nombre', read_only=True, default=None)
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    creado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = RegistroMantenimiento
        fields = [
            'id', 'herramienta', 'herramienta_codigo', 'herramienta_nombre',
            'plan', 'plan_nombre', 'tipo', 'tipo_display', 'estado', 'estado_display',
            'descripcion', 'fecha_inicio', 'fecha_fin', 'realizado_por', 'costo',
            'repuestos_usados', 'resultado', 'horas_herramienta_al_cierre',
            'motivo_cancelacion', 'creado_por_nombre',
        ]
        read_only_fields = fields

    def get_creado_por_nombre(self, obj):
        return _nombre(obj.creado_por)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # El costo solo lo ve quien tiene el permiso HERRAMIENTAS.COSTOS.VER.
        if not self.context.get('ver_costos', False):
            data.pop('costo', None)
        return data


class IniciarMantenimientoSerializer(serializers.Serializer):
    herramienta = serializers.IntegerField(min_value=1)
    tipo = serializers.ChoiceField(choices=RegistroMantenimiento.Tipo.choices)
    descripcion = serializers.CharField(max_length=1000)
    plan = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    realizado_por = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')

    def validate_descripcion(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('La descripción es obligatoria.')
        return value


class FinalizarMantenimientoSerializer(serializers.Serializer):
    resultado = serializers.CharField(max_length=1000)
    estado_fisico_final = serializers.ChoiceField(choices=Herramienta.EstadoFisico.choices)
    dejar_fuera_de_servicio = serializers.BooleanField(required=False, default=False)
    costo = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, allow_null=True, default=None
    )
    repuestos_usados = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
    realizado_por = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')

    def validate_resultado(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('El resultado es obligatorio.')
        return value


class CancelarMantenimientoSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=255)


class IncidenciaSerializer(serializers.ModelSerializer):
    herramienta_codigo = serializers.CharField(source='herramienta.codigo', read_only=True)
    herramienta_nombre = serializers.CharField(source='herramienta.nombre', read_only=True)
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    decision_display = serializers.CharField(source='get_decision_display', read_only=True)
    responsable_nombre = serializers.SerializerMethodField()
    reportada_por_nombre = serializers.SerializerMethodField()
    resuelta_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = IncidenciaHerramienta
        fields = [
            'id', 'herramienta', 'herramienta_codigo', 'herramienta_nombre',
            'tipo', 'tipo_display', 'estado', 'estado_display', 'descripcion', 'fecha',
            'responsable_nombre', 'reportada_por_nombre',
            'decision', 'decision_display', 'notas_resolucion',
            'resuelta_por_nombre', 'fecha_resolucion',
        ]
        read_only_fields = fields

    def get_responsable_nombre(self, obj):
        return _nombre(obj.responsable)

    def get_reportada_por_nombre(self, obj):
        return _nombre(obj.reportada_por)

    def get_resuelta_por_nombre(self, obj):
        return _nombre(obj.resuelta_por)


class CrearIncidenciaSerializer(serializers.Serializer):
    herramienta = serializers.IntegerField(min_value=1)
    tipo = serializers.ChoiceField(choices=IncidenciaHerramienta.Tipo.choices)
    descripcion = serializers.CharField(max_length=1000)

    def validate_descripcion(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('La descripción es obligatoria.')
        return value


class ResolverIncidenciaSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=IncidenciaHerramienta.Decision.choices)
    notas = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
