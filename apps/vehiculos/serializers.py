from rest_framework import serializers
from .models import Vehiculo, VehiculoQR, MantenimientoVehiculo
from django.utils import timezone

class VehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehiculo
        fields = '__all__'


class VehiculoQRSerializer(serializers.ModelSerializer):
    ruta_publica = serializers.SerializerMethodField()

    class Meta:
        model = VehiculoQR
        fields = [
            'id', 'vehiculo', 'token_publico', 'codigo_corto', 'activo',
            'fecha_creacion', 'fecha_actualizacion', 'fecha_revocacion',
            'ruta_publica',
        ]
        read_only_fields = fields

    def get_ruta_publica(self, obj):
        return f'/historial-vehiculo/{obj.token_publico}'

class MantenimientoVehiculoSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    intervalo_km = serializers.IntegerField(min_value=1, max_value=1000000, allow_null=True, required=False)
    intervalo_meses = serializers.IntegerField(min_value=1, max_value=120, allow_null=True, required=False)

    class Meta:
        model = MantenimientoVehiculo
        fields = [
            'id', 'tipo', 'tipo_display', 'fecha_realizado', 'kilometraje_realizado',
            'intervalo_km', 'intervalo_meses', 'fecha_creacion',
        ]
        read_only_fields = ['id', 'fecha_creacion']

    def validate_fecha_realizado(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('La fecha realizada no puede estar en el futuro.')
        return value

    def validate(self, attrs):
        km = attrs.get('intervalo_km', getattr(self.instance, 'intervalo_km', None))
        meses = attrs.get('intervalo_meses', getattr(self.instance, 'intervalo_meses', None))
        if not km and not meses:
            raise serializers.ValidationError('Indica un intervalo en kilometros, meses o ambos.')
        return attrs


from .models import VehiculoTransporte

class VehiculoTransporteSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehiculoTransporte
        fields = '__all__'
