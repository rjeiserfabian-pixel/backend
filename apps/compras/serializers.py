from rest_framework import serializers
from .models import Compra, DetalleCompra, CuentaPorPagar, PagoCuenta
from apps.clientes.serializers import ProveedorSerializer

class DetalleCompraSerializer(serializers.ModelSerializer):
    repuesto_codigo = serializers.ReadOnlyField(source='repuesto.codigo')
    repuesto_nombre = serializers.ReadOnlyField(source='repuesto.nombre')

    class Meta:
        model = DetalleCompra
        fields = '__all__'
        read_only_fields = ('compra',)


class CompraSerializer(serializers.ModelSerializer):
    detalles = DetalleCompraSerializer(many=True, required=False)
    proveedor_detalle = ProveedorSerializer(source='proveedor', read_only=True)
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')

    class Meta:
        model = Compra
        fields = '__all__'
        read_only_fields = ('usuario', 'creado_en')


class PagoCuentaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')

    class Meta:
        model = PagoCuenta
        fields = '__all__'
        read_only_fields = ('usuario', 'creado_en')


class CuentaPorPagarSerializer(serializers.ModelSerializer):
    pagos = PagoCuentaSerializer(many=True, read_only=True)
    proveedor_nombre = serializers.ReadOnlyField(source='proveedor.nombre_o_razon_social')
    compra_comprobante = serializers.SerializerMethodField()

    class Meta:
        model = CuentaPorPagar
        fields = '__all__'
        read_only_fields = ('actualizado_en', 'creado_en')

    def get_compra_comprobante(self, obj):
        return f"{obj.compra.tipo_comprobante} {obj.compra.serie}-{obj.compra.numero_comprobante}"
