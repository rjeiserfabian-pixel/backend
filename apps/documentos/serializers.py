import os

from rest_framework import serializers

from .models import Documento

TAMANO_MAXIMO = 5 * 1024 * 1024  # 5 MB
EXTENSIONES = {
    '.pdf': (b'%PDF',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.png': (b'\x89PNG',),
}


class DocumentoSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    estado_vigencia = serializers.CharField(read_only=True)
    dias_para_vencer = serializers.IntegerField(read_only=True)
    tiene_archivo = serializers.SerializerMethodField()
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True, default=None)
    cliente_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Documento
        fields = [
            'id', 'vehiculo', 'vehiculo_placa', 'cliente', 'cliente_nombre', 'tipo', 'tipo_display',
            'numero', 'entidad_emisora', 'fecha_emision', 'fecha_vencimiento', 'archivo',
            'tiene_archivo', 'nombre_archivo', 'observaciones', 'estado_vigencia', 'dias_para_vencer',
            'creado_en',
        ]
        read_only_fields = ['id', 'nombre_archivo', 'creado_en']
        extra_kwargs = {'archivo': {'write_only': True, 'required': False, 'allow_null': True}}

    def get_tiene_archivo(self, obj):
        return bool(obj.archivo)

    def get_cliente_nombre(self, obj):
        if not obj.cliente_id:
            return None
        return f'{obj.cliente.nombres} {obj.cliente.apellidos}'.strip()

    def validate_archivo(self, archivo):
        if archivo is None:
            return archivo
        extension = os.path.splitext(archivo.name)[1].lower()
        if extension not in EXTENSIONES:
            raise serializers.ValidationError('Solo se permiten archivos PDF, JPG o PNG.')
        if archivo.size > TAMANO_MAXIMO:
            raise serializers.ValidationError('El archivo no puede superar los 5 MB.')
        # No basta con confiar en la extensión: se comprueba la firma real del archivo.
        cabecera = archivo.read(8)
        archivo.seek(0)
        if not any(cabecera.startswith(firma) for firma in EXTENSIONES[extension]):
            raise serializers.ValidationError('El contenido del archivo no coincide con su formato.')
        return archivo

    def validate(self, attrs):
        instancia = self.instance
        vehiculo = attrs.get('vehiculo', getattr(instancia, 'vehiculo', None))
        cliente = attrs.get('cliente', getattr(instancia, 'cliente', None))
        if bool(vehiculo) == bool(cliente):
            raise serializers.ValidationError('El documento debe pertenecer a un vehículo o a un cliente (solo uno).')
        if instancia is not None and (
            ('vehiculo' in attrs and attrs['vehiculo'] != instancia.vehiculo)
            or ('cliente' in attrs and attrs['cliente'] != instancia.cliente)
        ):
            raise serializers.ValidationError('No se puede cambiar el propietario de un documento.')

        tipo = attrs.get('tipo', getattr(instancia, 'tipo', None))
        permitidos = Documento.TIPOS_VEHICULO if vehiculo else Documento.TIPOS_CLIENTE
        if tipo not in permitidos:
            raise serializers.ValidationError({'tipo': 'Este tipo de documento no aplica a este propietario.'})

        emision = attrs.get('fecha_emision', getattr(instancia, 'fecha_emision', None))
        vencimiento = attrs.get('fecha_vencimiento', getattr(instancia, 'fecha_vencimiento', None))
        if emision and vencimiento and vencimiento < emision:
            raise serializers.ValidationError({'fecha_vencimiento': 'El vencimiento no puede ser anterior a la emisión.'})
        return attrs
