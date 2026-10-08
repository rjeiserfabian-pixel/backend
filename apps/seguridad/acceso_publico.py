from ipaddress import ip_address
from urllib.parse import urlsplit

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ConfiguracionAccesoPublico
from .permissions import TienePermiso


class ConfiguracionAccesoPublicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfiguracionAccesoPublico
        fields = ['url_base', 'fecha_actualizacion']
        read_only_fields = ['fecha_actualizacion']

    def validate_url_base(self, value):
        value = value.strip()
        if not value:
            return ''
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise serializers.ValidationError('Ingresa una direccion publica HTTPS sin credenciales.')
        if url.path not in ('', '/') or url.query or url.fragment:
            raise serializers.ValidationError('Ingresa solo el dominio, sin rutas ni parametros.')
        host = url.hostname.lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
            raise serializers.ValidationError('La direccion debe ser publica, no una direccion local.')
        try:
            direccion = ip_address(host)
        except ValueError:
            direccion = None
        if direccion is not None and not direccion.is_global:
            raise serializers.ValidationError('No se permiten direcciones IP privadas.')
        return value.rstrip('/')


class ConfiguracionAccesoPublicoView(APIView):
    def get_permissions(self):
        codigo = 'CONFIGURACION.KIOSKOS.VER' if self.request.method == 'GET' else 'CONFIGURACION.KIOSKOS.EDITAR'
        return [TienePermiso(codigo)]

    def get(self, request):
        registro = ConfiguracionAccesoPublico.objects.filter(pk=1).first()
        return Response(ConfiguracionAccesoPublicoSerializer(registro).data if registro else {'url_base': '', 'fecha_actualizacion': None})

    def put(self, request):
        registro = ConfiguracionAccesoPublico.objects.filter(pk=1).first()
        serializer = ConfiguracionAccesoPublicoSerializer(registro, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(pk=1)
        return Response(serializer.data)


def obtener_url_publica():
    return ConfiguracionAccesoPublico.objects.filter(pk=1).values_list('url_base', flat=True).first() or ''
