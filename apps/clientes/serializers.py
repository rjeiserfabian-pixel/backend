from rest_framework import serializers
from .models import Cliente

class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = '__all__'

from .models import Proveedor

class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = '__all__'

from .models import Transportista

class TransportistaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transportista
        fields = '__all__'
