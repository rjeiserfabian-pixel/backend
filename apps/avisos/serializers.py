from rest_framework import serializers

from .models import AvisoCliente
from .servicios import whatsapp_url


class AvisoSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    whatsapp_url = serializers.SerializerMethodField()
    atendido_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = AvisoCliente
        fields = [
            'id', 'tipo', 'tipo_display', 'cliente', 'cliente_nombre', 'telefono', 'mensaje',
            'fecha_evento', 'estado', 'estado_display', 'descartado_automatico', 'whatsapp_url',
            'atendido_por_nombre', 'atendido_en', 'creado_en',
        ]
        read_only_fields = fields

    def get_whatsapp_url(self, obj):
        return whatsapp_url(obj.telefono, obj.mensaje)

    def get_atendido_por_nombre(self, obj):
        usuario = obj.atendido_por
        if not usuario:
            return None
        return f'{usuario.nombres} {usuario.apellidos}'.strip() or usuario.username
