"""
serializers.py — Módulo de Cajas
Serializa los modelos de ventas relacionados con cajas para la API del módulo.
"""
from rest_framework import serializers
from apps.ventas.models import (
    Caja, SesionCaja, MovimientoCaja, MetodoPago,
    TransferenciaCaja, ArqueoCaja
)


class CajaSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = Caja
        fields = ['id', 'nombre', 'tipo', 'tipo_display', 'sucursal', 'sucursal_nombre',
                  'almacen_defecto', 'estado']


class SesionCajaResumenSerializer(serializers.ModelSerializer):
    """Serializer liviano para usar en listas y dashboard."""
    caja_nombre = serializers.CharField(source='caja.nombre', read_only=True)
    caja_tipo   = serializers.CharField(source='caja.tipo', read_only=True)
    usuario_nombre = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)

    class Meta:
        model = SesionCaja
        fields = [
            'id', 'caja', 'caja_nombre', 'caja_tipo',
            'usuario', 'usuario_nombre',
            'fecha_apertura', 'fecha_cierre',
            'saldo_inicial', 'saldo_cierre_esperado', 'saldo_cierre_real',
            'estado', 'estado_display',
        ]

    def get_usuario_nombre(self, obj):
        return f"{obj.usuario.nombres} {obj.usuario.apellidos}".strip() or obj.usuario.username


class MovimientoCajaSerializer(serializers.ModelSerializer):
    metodo_pago_nombre  = serializers.CharField(source='metodo_pago.nombre', read_only=True)
    tipo_display        = serializers.CharField(source='get_tipo_display', read_only=True)
    concepto_display    = serializers.CharField(source='get_concepto_display', read_only=True)
    origen_display      = serializers.CharField(source='get_origen_movimiento_display', read_only=True)
    estado_display      = serializers.CharField(source='get_estado_movimiento_display', read_only=True)
    creado_por_nombre   = serializers.SerializerMethodField()
    aprobado_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoCaja
        fields = [
            'id', 'sesion',
            'tipo', 'tipo_display',
            'concepto', 'concepto_display',
            'origen_movimiento', 'origen_display',
            'referencia_origen',
            'estado_movimiento', 'estado_display',
            'metodo_pago', 'metodo_pago_nombre',
            'monto', 'numero_recibo', 'referencia',
            'observacion',
            'aprobado_por', 'aprobado_por_nombre',
            'creado_por', 'creado_por_nombre',
            'fecha',
        ]
        read_only_fields = ['numero_recibo', 'fecha', 'creado_por', 'aprobado_por']

    def get_creado_por_nombre(self, obj):
        return f"{obj.creado_por.nombres} {obj.creado_por.apellidos}".strip() or obj.creado_por.username

    def get_aprobado_por_nombre(self, obj):
        if obj.aprobado_por:
            return f"{obj.aprobado_por.nombres} {obj.aprobado_por.apellidos}".strip() or obj.aprobado_por.username
        return None


class MovimientoCajaCreateSerializer(serializers.ModelSerializer):
    """Serializer exclusivo para registrar movimientos manuales."""
    class Meta:
        model = MovimientoCaja
        fields = [
            'sesion', 'tipo', 'concepto',
            'origen_movimiento', 'referencia_origen',
            'metodo_pago', 'monto', 'referencia', 'observacion',
        ]

    def validate(self, attrs):
        concepto = attrs.get('concepto')
        # Los movimientos manuales entran siempre como PENDIENTE
        manuales = [
            MovimientoCaja.Concepto.INGRESO_MANUAL,
            MovimientoCaja.Concepto.EGRESO_MANUAL,
        ]
        attrs['estado_movimiento'] = (
            MovimientoCaja.EstadoMovimiento.PENDIENTE
            if concepto in manuales
            else MovimientoCaja.EstadoMovimiento.APROBADO
        )
        return attrs


class TransferenciaCajaSerializer(serializers.ModelSerializer):
    caja_origen_nombre  = serializers.CharField(source='sesion_origen.caja.nombre', read_only=True)
    caja_destino_nombre = serializers.CharField(source='sesion_destino.caja.nombre', read_only=True)
    usuario_nombre      = serializers.SerializerMethodField()
    estado_display      = serializers.CharField(source='get_estado_display', read_only=True)

    class Meta:
        model = TransferenciaCaja
        fields = [
            'id',
            'sesion_origen', 'caja_origen_nombre',
            'sesion_destino', 'caja_destino_nombre',
            'monto', 'motivo', 'estado', 'estado_display',
            'usuario', 'usuario_nombre', 'fecha',
        ]
        read_only_fields = ['estado', 'usuario', 'fecha',
                            'movimiento_salida', 'movimiento_entrada']

    def get_usuario_nombre(self, obj):
        return f"{obj.usuario.nombres} {obj.usuario.apellidos}".strip() or obj.usuario.username


class ArqueoCajaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()

    class Meta:
        model = ArqueoCaja
        fields = [
            'id', 'sesion',
            'saldo_teorico', 'saldo_contado', 'diferencia',
            'motivo_diferencia', 'es_cierre_final',
            'usuario', 'usuario_nombre', 'fecha',
        ]
        read_only_fields = ['saldo_teorico', 'diferencia', 'usuario', 'fecha']

    def get_usuario_nombre(self, obj):
        return f"{obj.usuario.nombres} {obj.usuario.apellidos}".strip() or obj.usuario.username


class MetodoPagoSerializer(serializers.ModelSerializer):
    class Meta:
        model = MetodoPago
        fields = ['id', 'nombre', 'requiere_referencia', 'estado']
