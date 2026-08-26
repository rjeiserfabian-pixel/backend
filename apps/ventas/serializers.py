from rest_framework import serializers
from .models import (
    Caja, SesionCaja, MovimientoCaja, TipoComprobante, SerieComprobante, MetodoPago, 
    Impuesto, Venta, DetalleVenta, PagoVenta, CuentaPorCobrar, CuotaCredito
)
from apps.inventario.serializers import RepuestoSerializer
from apps.clientes.serializers import ClienteSerializer
from apps.vehiculos.serializers import VehiculoSerializer


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
    cliente_nombre = serializers.CharField(source='cliente.nombres', read_only=True)
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True)
    
    class Meta:
        model = Venta
        fields = '__all__'


class TicketKioskoCreateSerializer(serializers.Serializer):
    cliente_id = serializers.IntegerField()
    vehiculo_id = serializers.IntegerField()
    sucursal_id = serializers.IntegerField()
    detalles = serializers.ListField(
        child=serializers.DictField()
    )
    # detalles format: [{"repuesto_id": 1, "cantidad": 2, "precio_unitario": 10.50}]


class ProcesarVentaSerializer(serializers.Serializer):
    tipo_comprobante = serializers.CharField(max_length=30)
    pagos = serializers.ListField(
        child=serializers.DictField()
    )
    # pagos format: [{"metodo_pago_id": 1, "monto": 50.00, "referencia": "op-123"}]
    credito = serializers.DictField(required=False)
    # credito format: {"frecuencia": "MENSUAL", "cuotas": 3}
    almacen_origen_id = serializers.IntegerField(required=False)


# ──────────────────────────────────────────────
# CRÉDITOS Y COBRANZAS
# ──────────────────────────────────────────────

class CuotaCreditoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CuotaCredito
        fields = '__all__'


class CuentaPorCobrarSerializer(serializers.ModelSerializer):
    cuotas = CuotaCreditoSerializer(many=True, read_only=True)
    cliente_nombre = serializers.CharField(source='venta.cliente.nombres', read_only=True)

    class Meta:
        model = CuentaPorCobrar
        fields = '__all__'

