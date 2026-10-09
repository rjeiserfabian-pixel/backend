from django.db.models import Q
from rest_framework import pagination, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Auditoria
from .permissions import TienePermiso


class AuditoriaPagination(pagination.PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class AuditoriaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()
    usuario_username = serializers.CharField(source='id_usuario.username', read_only=True, default=None)

    class Meta:
        model = Auditoria
        fields = [
            'id_auditoria', 'fecha', 'usuario_nombre', 'usuario_username', 'modulo', 'accion',
            'tabla_afectada', 'registro_id', 'datos_anteriores', 'datos_nuevos', 'ip',
        ]
        read_only_fields = fields

    def get_usuario_nombre(self, obj):
        usuario = obj.id_usuario
        if usuario is None:
            return None
        return f'{usuario.nombres} {usuario.apellidos}'.strip() or usuario.username


class AuditoriaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Consulta de la auditoría (solo lectura: el registro es inmutable).
    Filtros: usuario (texto), modulo, accion, fecha_desde, fecha_hasta, search (tabla / id de registro).
    """
    serializer_class = AuditoriaSerializer
    pagination_class = AuditoriaPagination

    def get_permissions(self):
        return [TienePermiso('SEGURIDAD.AUDITORIA.VER')]

    def get_queryset(self):
        qs = Auditoria.objects.select_related('id_usuario').order_by('-fecha', '-id_auditoria')
        p = self.request.query_params
        if p.get('modulo'):
            qs = qs.filter(modulo=p['modulo'])
        if p.get('accion'):
            qs = qs.filter(accion=p['accion'])
        if p.get('usuario'):
            texto = p['usuario'].strip()
            qs = qs.filter(Q(id_usuario__username__icontains=texto) | Q(id_usuario__nombres__icontains=texto)
                           | Q(id_usuario__apellidos__icontains=texto))
        if p.get('fecha_desde'):
            qs = qs.filter(fecha__date__gte=p['fecha_desde'])
        if p.get('fecha_hasta'):
            qs = qs.filter(fecha__date__lte=p['fecha_hasta'])
        if p.get('search'):
            texto = p['search'].strip()
            qs = qs.filter(Q(tabla_afectada__icontains=texto) | Q(registro_id__iexact=texto))
        return qs

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        """Módulos y acciones que existen en la auditoría (para los filtros de la pantalla)."""
        return Response({
            'modulos': list(Auditoria.objects.order_by().values_list('modulo', flat=True).distinct().order_by('modulo')),
            'acciones': list(Auditoria.objects.order_by().values_list('accion', flat=True).distinct().order_by('accion')),
        })
