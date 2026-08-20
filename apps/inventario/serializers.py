from rest_framework import serializers
from .models import Categoria, MarcaRepuesto, Repuesto, AplicacionRepuesto

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
        exclude = ('repuesto',) # Se excluye porque se asociará al crear el repuesto


class RepuestoSerializer(serializers.ModelSerializer):
    aplicaciones = AplicacionRepuestoSerializer(many=True, required=False)
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    marca_nombre = serializers.CharField(source='marca.nombre', read_only=True)

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
