from rest_framework import serializers
from .models import Vehiculo

class VehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehiculo
        fields = '__all__'

from .models import VehiculoTransporte

class VehiculoTransporteSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehiculoTransporte
        fields = '__all__'
