from rest_framework import serializers

from .models import ComprobanteElectronico, ComprobanteElectronicoDetalle, ComprobanteEnvioLog


class ComprobanteEnvioLogSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.CharField(source='usuario.username', read_only=True, default='')

    class Meta:
        model = ComprobanteEnvioLog
        fields = ['id', 'endpoint', 'payload_enviado', 'respuesta_api', 'exitoso', 'mensaje', 'usuario_nombre', 'fecha']


class ComprobanteElectronicoDetalleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComprobanteElectronicoDetalle
        exclude = ('comprobante',)


class ComprobanteElectronicoSerializer(serializers.ModelSerializer):
    tipo_documento_display = serializers.CharField(source='get_tipo_documento_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    comprobante_relacionado_codigo = serializers.SerializerMethodField()
    logs = ComprobanteEnvioLogSerializer(many=True, read_only=True)
    detalles = ComprobanteElectronicoDetalleSerializer(many=True, read_only=True)

    class Meta:
        model = ComprobanteElectronico
        fields = '__all__'
        read_only_fields = [
            'tipo_documento', 'serie', 'numero', 'sucursal', 'venta', 'guia_remision',
            'comprobante_relacionado', 'motivo_codigo', 'cliente_tipo_documento', 'cliente_documento',
            'cliente_nombre', 'moneda', 'total', 'estado', 'payload_enviado', 'respuesta_api',
            'mensaje_error', 'pdf_url', 'xml_url', 'cdr_url', 'hash_cdr', 'ticket_sunat',
            'intentos', 'creado_por', 'enviado_por', 'creado_en', 'fecha_envio', 'fecha_respuesta',
        ]

    def get_comprobante_relacionado_codigo(self, obj):
        if obj.comprobante_relacionado_id:
            rel = obj.comprobante_relacionado
            return f"{rel.serie}-{rel.numero}"
        return None
